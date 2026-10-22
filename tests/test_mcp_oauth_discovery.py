from mcp_oauth import hash_token, new_token, pkce_s256, valid_redirect_uri


def test_token_helpers():
    t = new_token()
    assert len(t) >= 43 and new_token() != t
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    # RFC 7636 appendix B test vector
    assert pkce_s256("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk") == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def test_redirect_uri_rules():
    assert valid_redirect_uri("https://claude.ai/api/mcp/auth_callback")
    assert valid_redirect_uri("http://localhost:6274/oauth/callback")
    assert valid_redirect_uri("http://127.0.0.1:33418/callback")
    assert not valid_redirect_uri("http://evil.example/cb")
    assert not valid_redirect_uri("https://claude.ai/cb#frag")
    assert not valid_redirect_uri("javascript:alert(1)")
    assert not valid_redirect_uri("")
    # Consent-screen spoofing: the host shown must be the host the code goes to.
    assert not valid_redirect_uri("https://claude.ai@evil.example/cb")      # userinfo
    assert not valid_redirect_uri("https://evil.example\\@claude.ai/cb")   # backslash
    assert not valid_redirect_uri("https://:443/cb")                        # empty hostname
    assert not valid_redirect_uri("https://claude.ai /cb")                  # whitespace
    assert not valid_redirect_uri("https://clаude.ai/cb")               # Cyrillic a
    assert not valid_redirect_uri("https://claude.ai%2eevil.example/cb")    # percent-encoded host
    assert valid_redirect_uri("http://localhost:6274/cb")


def test_protected_resource_metadata(client):
    for path in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-protected-resource/mcp"):
        j = client.get(path).get_json()
        assert j["resource"] == "http://localhost/mcp"
        assert j["authorization_servers"] == ["http://localhost"]


def test_authorization_server_metadata(client):
    j = client.get("/.well-known/oauth-authorization-server").get_json()
    assert j["issuer"] == "http://localhost"
    assert j["authorization_endpoint"] == "http://localhost/oauth/authorize"
    assert j["token_endpoint"] == "http://localhost/oauth/token"
    assert j["registration_endpoint"] == "http://localhost/oauth/register"
    assert j["code_challenge_methods_supported"] == ["S256"]
    assert j["token_endpoint_auth_methods_supported"] == ["none"]
    assert j["authorization_response_iss_parameter_supported"] is True


def test_register_public_client(client):
    import app as appmod
    r = client.post("/oauth/register", json={
        "client_name": "Claude", "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
        "token_endpoint_auth_method": "client_secret_post"})
    assert r.status_code == 201
    j = r.get_json()
    assert j["token_endpoint_auth_method"] == "none"   # replaced: public clients only
    assert "client_secret" not in j
    row = appmod.McpClient.query.filter_by(client_id=j["client_id"]).first()
    assert row.client_name == "Claude"
    assert row.redirect_uris == ["https://claude.ai/api/mcp/auth_callback"]


def test_register_rejects_bad_redirects(client):
    for uris in (None, [], ["http://evil.example/cb"], ["https://ok.example/cb"] * 11):
        r = client.post("/oauth/register", json={"client_name": "x", "redirect_uris": uris})
        assert r.status_code == 400
        assert r.get_json()["error"] == "invalid_redirect_uri"


def test_cors_on_discovery_and_token(client):
    r = client.get("/.well-known/oauth-authorization-server", headers={"Origin": "https://inspector.example"})
    assert r.headers.get("Access-Control-Allow-Origin") == "*"


def test_register_truncates_long_client_name(client):
    r = client.post("/oauth/register", json={
        "client_name": "A" * 200, "redirect_uris": ["https://claude.ai/cb"]})
    assert r.status_code == 201
    assert r.get_json()["client_name"] == "A" * 60
    import app as appmod
    assert appmod.McpClient.query.one().client_name == "A" * 60
