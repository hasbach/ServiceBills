"""OAuth 2.1 authorization server for the MCP connector -- see
docs/superpowers/specs/2026-10-21-mcp-connector-design.md. Registered from
app.py via register_mcp_oauth_routes(app, appmod) so app.py doesn't grow.

Public clients only (PKCE S256, no client secrets). Tokens are random
strings; only their SHA-256 hashes are stored (McpGrant)."""
import base64
import hashlib
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

from flask import Response, jsonify, redirect, request
from flask_jwt_extended import get_jwt_identity, jwt_required

import modules

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


_CHALLENGE_RE = re.compile(r'^[A-Za-z0-9_-]{43,128}$')


def with_query(uri, params):
    """Append params to a redirect URI that may already carry a query."""
    clean = {k: v for k, v in params.items() if v is not None}
    return uri + ('&' if '?' in uri else '?') + urlencode(clean)


def validate_authorize_params(params, expected_resource):
    """(error, description) for a bad authorization request, else None.
    Assumes client_id and redirect_uri were already checked."""
    if params.get('response_type') != 'code':
        return 'unsupported_response_type', 'response_type must be "code"'
    if params.get('code_challenge_method') != 'S256' or not _CHALLENGE_RE.match(
            str(params.get('code_challenge') or '')):
        return 'invalid_request', 'PKCE with code_challenge_method=S256 is required'
    resource = params.get('resource')
    if resource and str(resource).rstrip('/') != expected_resource:
        return 'invalid_target', 'This server only issues tokens for ' + expected_resource
    return None


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

    Grant = appmod.McpGrant

    def _client_and_redirect(params):
        """(client, error message) -- client_id/redirect_uri must match a
        registration exactly; errors here are never redirected."""
        client = Client.query.filter_by(client_id=str(params.get('client_id') or '')).first()
        if client is None:
            return None, 'Unknown AI app (client_id). Remove the connector in your AI app and add it again.'
        if params.get('redirect_uri') not in (client.redirect_uris or []):
            return None, "redirect_uri doesn't match the one this AI app registered."
        return client, None

    def _consenting_user():
        """(user, tenant, error message) for the logged-in ServiceBills user."""
        user = appmod.User.query.filter_by(username=get_jwt_identity()).first()
        if user is None or user.tenant_id is None:
            return None, None, "Sign in with your business account to connect an AI app."
        tenant = db.session.get(appmod.Tenant, user.tenant_id)
        if tenant is None or tenant.status != 'active':
            return None, None, "This business's subscription is inactive."
        if not modules.is_enabled(tenant, 'ai_connector'):
            return None, None, ("The AI connector is not part of this business's plan. "
                                "Ask ServiceBills to enable it.")
        return user, tenant, None

    @app.route('/oauth/authorize', methods=['GET'])
    def mcp_oauth_authorize():
        params = {k: request.args.get(k) for k in AUTHORIZE_KEYS}
        client, problem = _client_and_redirect(params)
        if problem:
            return Response(problem, status=400, mimetype='text/plain')
        bad = validate_authorize_params(params, origin() + '/mcp')
        if bad:
            return redirect(with_query(params['redirect_uri'], {
                'error': bad[0], 'error_description': bad[1],
                'state': params.get('state'), 'iss': origin()}))
        return redirect('/oauth/consent?' + urlencode({k: v for k, v in params.items() if v is not None}))

    @app.route('/api/mcp/oauth/consent-info', methods=['GET'])
    @jwt_required()
    def mcp_oauth_consent_info():
        params = {k: request.args.get(k) for k in AUTHORIZE_KEYS}
        client, problem = _client_and_redirect(params)
        if not problem:
            bad = validate_authorize_params(params, origin() + '/mcp')
            problem = bad[1] if bad else None
        if problem:
            return jsonify(error=problem), 400
        user, tenant, problem = _consenting_user()
        if problem:
            return jsonify(error=problem), 403
        return jsonify(client_name=client.client_name, tenant_name=tenant.name,
                       username=user.username, role=user.role,
                       redirect_host=urlparse(params['redirect_uri']).netloc)

    @app.route('/api/mcp/oauth/approve', methods=['POST'])
    @jwt_required()
    def mcp_oauth_approve():
        data = request.get_json(silent=True) or {}
        params = {k: (str(data[k]) if data.get(k) is not None else None) for k in AUTHORIZE_KEYS}
        client, problem = _client_and_redirect(params)
        if not problem:
            bad = validate_authorize_params(params, origin() + '/mcp')
            problem = bad[1] if bad else None
        if problem:
            return jsonify(error=problem), 400
        if data.get('decision') != 'allow':
            return jsonify(redirect_url=with_query(params['redirect_uri'], {
                'error': 'access_denied', 'state': params.get('state'), 'iss': origin()}))
        user, tenant, problem = _consenting_user()
        if problem:
            return jsonify(error=problem), 403
        code = new_token()
        db.session.add(Grant(
            tenant_id=tenant.id, user_id=user.id, client_id=client.client_id, scope=SCOPE,
            resource=params.get('resource'), code_hash=hash_token(code),
            code_challenge=params['code_challenge'], redirect_uri=params['redirect_uri'],
            code_expires_at=datetime.utcnow() + CODE_TTL))
        db.session.commit()
        return jsonify(redirect_url=with_query(params['redirect_uri'], {
            'code': code, 'state': params.get('state'), 'iss': origin()}))
