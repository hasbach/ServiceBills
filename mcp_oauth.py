"""OAuth 2.1 authorization server for the MCP connector -- see
docs/superpowers/specs/2026-10-21-mcp-connector-design.md. Registered from
app.py via register_mcp_oauth_routes(app, appmod) so app.py doesn't grow.

Public clients only (PKCE S256, no client secrets). Tokens are random
strings; only their SHA-256 hashes are stored (McpGrant)."""
import base64
import hashlib
import secrets
import time
from datetime import timedelta
from urllib.parse import urlparse

from flask import jsonify, request

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=30)
CODE_TTL = timedelta(seconds=60)
SCOPE = 'read'
AUTHORIZE_KEYS = ('client_id', 'redirect_uri', 'response_type', 'code_challenge',
                  'code_challenge_method', 'state', 'scope', 'resource')
_LOOPBACK = ('localhost', '127.0.0.1', '::1')


def new_token():
    return secrets.token_urlsafe(32)


def hash_token(token):
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def pkce_s256(verifier):
    digest = hashlib.sha256(verifier.encode('ascii')).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b'=').decode('ascii')


def origin():
    """This server's public origin as the client reached it. Behind Render
    (TRUST_PROXY=1 -> ProxyFix x_proto) this is the https URL."""
    return request.url_root.rstrip('/')


def valid_redirect_uri(uri):
    if not isinstance(uri, str) or not uri:
        return False
    try:
        p = urlparse(uri)
    except ValueError:
        return False
    if p.fragment:
        return False
    if p.scheme == 'https' and p.netloc:
        return True
    return p.scheme == 'http' and p.hostname in _LOOPBACK


def oauth_error(code, description, status=400):
    resp = jsonify(error=code, error_description=description)
    resp.status_code = status
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def register_mcp_oauth_routes(app, appmod):
    db = appmod.db
    Client = appmod.McpClient

    @app.route('/.well-known/oauth-protected-resource', methods=['GET'])
    @app.route('/.well-known/oauth-protected-resource/mcp', methods=['GET'])
    def mcp_protected_resource_metadata():
        o = origin()
        return jsonify({'resource': o + '/mcp', 'authorization_servers': [o],
                        'scopes_supported': [SCOPE], 'bearer_methods_supported': ['header'],
                        'resource_name': 'ServiceBills'})

    @app.route('/.well-known/oauth-authorization-server', methods=['GET'])
    def mcp_authorization_server_metadata():
        o = origin()
        return jsonify({
            'issuer': o,
            'authorization_endpoint': o + '/oauth/authorize',
            'token_endpoint': o + '/oauth/token',
            'registration_endpoint': o + '/oauth/register',
            'revocation_endpoint': o + '/oauth/revoke',
            'response_types_supported': ['code'],
            'grant_types_supported': ['authorization_code', 'refresh_token'],
            'code_challenge_methods_supported': ['S256'],
            'token_endpoint_auth_methods_supported': ['none'],
            'revocation_endpoint_auth_methods_supported': ['none'],
            'scopes_supported': [SCOPE],
        })

    @app.route('/oauth/register', methods=['POST'])
    @appmod.limiter.limit('20/hour')
    def mcp_oauth_register():
        data = request.get_json(silent=True) or {}
        uris = data.get('redirect_uris')
        if (not isinstance(uris, list) or not uris or len(uris) > 10
                or not all(isinstance(u, str) and len(u) <= 500 and valid_redirect_uri(u) for u in uris)):
            return oauth_error('invalid_redirect_uri',
                               'redirect_uris must be 1-10 https:// or loopback http:// URLs')
        name = str(data.get('client_name') or 'AI app').strip()[:200] or 'AI app'
        client = Client(client_id=secrets.token_urlsafe(24), client_name=name, redirect_uris=uris)
        db.session.add(client)
        db.session.commit()
        # RFC 7591 lets the server replace requested metadata: whatever auth
        # method was asked for, this is a public client with no secret.
        resp = jsonify({'client_id': client.client_id, 'client_name': name, 'redirect_uris': uris,
                        'grant_types': ['authorization_code', 'refresh_token'],
                        'response_types': ['code'], 'token_endpoint_auth_method': 'none',
                        'client_id_issued_at': int(time.time())})
        resp.status_code = 201
        resp.headers['Cache-Control'] = 'no-store'
        return resp
