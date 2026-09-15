"""Tests for the self-orchestrated Gemini CS agent brain (query_gemini_agent)."""
from unittest.mock import patch, MagicMock
import pytest
import app as appmod
import cs_agent_tools


def _text_only_response(text):
    resp = MagicMock()
    resp.function_calls = None
    resp.text = text
    return resp


def _fake_api_error(code, message):
    """Builds a genuine errors.APIError instance without depending on its
    real __init__ signature (undocumented/unstable across SDK versions) --
    query_gemini_agent's `except errors.APIError as e` only needs isinstance()
    to hold and `.code`/`.message` to be readable, both set directly here."""
    from google.genai import errors
    err = errors.APIError.__new__(errors.APIError)
    err.code = code
    err.message = message
    return err


def test_query_gemini_agent_returns_none_without_api_key():
    result = cs_agent_tools.query_gemini_agent(
        appmod, tenant_id=1, api_key=None, incoming_text="شو رصيدي؟", sender_phone="70123456"
    )
    assert result is None


def test_query_gemini_agent_returns_none_without_incoming_text():
    result = cs_agent_tools.query_gemini_agent(
        appmod, tenant_id=1, api_key="fake-key", incoming_text="", sender_phone="70123456"
    )
    assert result is None


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
def test_query_gemini_agent_simple_text_reply(mock_search):
    """No tool calls -- Gemini answers directly, and we return the same dict
    shape query_elevenlabs_conversational_ai used to return."""
    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.return_value = _text_only_response("أهلاً! رصيدك صفر.")

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=1, api_key="fake-key",
            incoming_text="شو رصيدي؟", sender_phone="70123456"
        )

    assert result is not None
    assert result["reply_text"] == "أهلاً! رصيدك صفر."
    assert result["intent"] == "gemini_agent"
    assert result["escalate"] is False


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
@patch("cs_agent_tools.get_customer_status")
def test_query_gemini_agent_dispatches_tool_call_then_returns_final_text(mock_get_status, mock_search):
    """A single function-call turn: Gemini asks for get_customer_status, we
    call the real tool dispatcher, feed the result back, and Gemini's second
    response (no more function calls) becomes the final reply."""
    mock_get_status.return_value = {"found": True, "balance_due": 25, "expiry_date": "2026-10-01"}

    fake_function_call = MagicMock()
    fake_function_call.name = "get_customer_status"
    fake_function_call.args = {"customer_id": 42}

    first_response = MagicMock()
    first_response.function_calls = [fake_function_call]
    first_response.candidates = [MagicMock(content="model-turn-with-function-call")]

    second_response = _text_only_response("رصيدك المتبقي 25 دولار وبينتهي بـ 2026-10-01.")

    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.side_effect = [first_response, second_response]

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=7, api_key="fake-key",
            incoming_text="شو باقي علي؟", sender_phone="70123456",
            customer=MagicMock(id=42, name="Georges")
        )

    assert result["reply_text"] == "رصيدك المتبقي 25 دولار وبينتهي بـ 2026-10-01."
    mock_get_status.assert_called_once_with(appmod, 7, 42)


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
def test_query_gemini_agent_falls_back_to_lite_model_on_rate_limit(mock_search):
    """A 429 on the primary model retries once on the fallback model instead
    of giving up immediately."""
    rate_limit_error = _fake_api_error(429, "rate limited")

    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.side_effect = [
            rate_limit_error,
            _text_only_response("جواب من الموديل الاحتياطي."),
        ]

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=1, api_key="fake-key",
            incoming_text="شو رصيدي؟", sender_phone="70123456"
        )

    assert result["reply_text"] == "جواب من الموديل الاحتياطي."
    assert instance.models.generate_content.call_count == 2
    second_call_model = instance.models.generate_content.call_args_list[1].kwargs.get("model")
    assert second_call_model == cs_agent_tools.GEMINI_MODEL_FALLBACK


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
def test_query_gemini_agent_returns_none_on_non_rate_limit_error(mock_search):
    """Any other API error (not 429) returns None immediately -- caller falls
    back to the rule-based processor, no retry on the fallback model."""
    server_error = _fake_api_error(500, "internal error")

    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.side_effect = [server_error]

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=1, api_key="fake-key",
            incoming_text="شو رصيدي؟", sender_phone="70123456"
        )

    assert result is None
    assert instance.models.generate_content.call_count == 1


def test_dispatch_gemini_tool_missing_customer_id_returns_error_dict_instead_of_raising():
    """get_customer_status requires customer_id. If Gemini's function call
    omits it (a plausible mistake, especially from the flash-lite fallback
    model before it has looked the customer up), int(None) must not be
    allowed to raise TypeError and blow up the whole tool-dispatch loop --
    it should come back as an error dict Gemini can see and retry from,
    the same way escalate_to_human already handles a missing customer_id."""
    result = cs_agent_tools._dispatch_gemini_tool(
        appmod, tenant_id=1, sender_phone="70123456",
        tool_name="get_customer_status", tool_args={}
    )
    assert isinstance(result, dict)
    assert "error" in result


def test_dispatch_gemini_tool_none_customer_id_returns_error_dict_instead_of_raising():
    """Same as above but customer_id explicitly present with value None
    (as opposed to the key being absent entirely)."""
    result = cs_agent_tools._dispatch_gemini_tool(
        appmod, tenant_id=1, sender_phone="70123456",
        tool_name="network_diagnostic", tool_args={"customer_id": None}
    )
    assert isinstance(result, dict)
    assert "error" in result


def test_dispatch_gemini_tool_non_numeric_customer_id_returns_error_dict_instead_of_raising():
    """A non-numeric customer_id (e.g. Gemini hallucinating a string) must
    also come back as an error dict rather than raising ValueError."""
    result = cs_agent_tools._dispatch_gemini_tool(
        appmod, tenant_id=1, sender_phone="70123456",
        tool_name="send_payment_link", tool_args={"customer_id": "not-a-number"}
    )
    assert isinstance(result, dict)
    assert "error" in result


@patch("cs_agent_tools.search_knowledge_entries", return_value=[])
@patch("cs_agent_tools.get_customer_status")
def test_query_gemini_agent_roundtrip_budget_is_global_across_model_fallback(mock_get_status, mock_search):
    """The plan's stated cap ("Tool-call loop cap: 6 round-trips") is a single
    hard total, not a per-model-attempt allowance. This simulates 4 tool
    round-trips on the primary model, then a 429 that switches to the
    fallback model -- the fallback must only get the 2 round-trips left in
    the shared budget (for 6 total), never a fresh 6 (which would allow 12
    total across both models).

    Every simulated model response keeps asking for another tool call (it
    never naturally stops), so the ONLY thing that can end the loop is the
    round-trip budget itself. The side_effect list below has exactly as many
    entries as the correct global-budget behavior consumes (8 generate_content
    calls: 4 primary + 1 rate-limit + 2 fallback tool rounds + 1 final
    call). A per-model-reset bug would keep looping past that and either
    exhaust the side_effect list (raising StopIteration on a 9th call) or
    otherwise make more than 6 tool-dispatch calls -- so pinning both
    call_count numbers below the "6/8" line to this exact scenario positively
    detects a reset in either direction, not just a difference count."""
    mock_get_status.return_value = {"found": True, "balance_due": 25}

    fake_function_call = MagicMock()
    fake_function_call.name = "get_customer_status"
    fake_function_call.args = {"customer_id": 42}

    def fc_response():
        resp = MagicMock()
        resp.function_calls = [fake_function_call]
        resp.candidates = [MagicMock(content="model-turn-with-function-call")]
        resp.text = None
        return resp

    rate_limit_error = _fake_api_error(429, "rate limited")

    with patch("google.genai.Client") as MockClient:
        instance = MockClient.return_value
        instance.models.generate_content.side_effect = [
            fc_response(), fc_response(), fc_response(), fc_response(),  # 4 rounds, primary model
            rate_limit_error,  # 429 -- falls back to the lite model
            fc_response(), fc_response(),  # only 2 more rounds allowed (budget: 6 total, not 6 fresh)
            fc_response(),  # still asking for tools, but budget is exhausted -- loop must stop here
        ]

        result = cs_agent_tools.query_gemini_agent(
            appmod, tenant_id=7, api_key="fake-key",
            incoming_text="شو رصيدي؟", sender_phone="70123456"
        )

    # Budget exhausted without ever getting a final text reply -> caller
    # falls back to the rule-based processor.
    assert result is None
    assert mock_get_status.call_count == 6
    assert instance.models.generate_content.call_count == 8
