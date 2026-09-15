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
