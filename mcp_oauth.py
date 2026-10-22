"""OAuth 2.1 authorization server for the MCP connector -- see
docs/superpowers/specs/2026-10-21-mcp-connector-design.md. Registered from
app.py via register_mcp_oauth_routes(app, appmod) so app.py doesn't grow.

Public clients only (PKCE S256, no client secrets). Tokens are random
strings; only their SHA-256 hashes are stored (McpGrant)."""
import base64
import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode, urlparse

from flask import Response, jsonify, redirect, request
from flask_jwt_extended import get_jwt_identity, jwt_required

import modules
from tenancy import current_tenant, tenant_query

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
    """https with a host, or http loopback; no fragment. Also rejects every
    shape the consent screen could be fooled by: userinfo
    (https://claude.ai@evil.example), backslashes (parsers disagree on where
    the host ends), an empty hostname, and whitespace/non-ASCII (homoglyph
    hosts)."""
    if not isinstance(uri, str) or not uri:
        return False
    if '\\' in uri or any(ord(c) <= 0x20 or ord(c) >= 0x7f for c in uri):
        return False
    try:
        p = urlparse(uri)
        p.port   # raises ValueError on a malformed port
    except ValueError:
        return False
    if p.fragment or p.username is not None or p.password is not None or not p.hostname:
        return False
    if p.scheme == 'https':
        return True
    return p.scheme == 'http' and p.hostname in _LOOPBACK


def redirect_host(uri):
    """What the consent screen shows as the destination: hostname, plus the
    port only when one is explicitly present."""
    p = urlparse(uri)
    host = p.hostname or ''
    if ':' in host:
        host = '[' + host + ']'
    return host + (':' + str(p.port) if p.port is not None else '')


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


_VERIFIER_RE = re.compile(r'^[A-Za-z0-9\-._~]{43,128}$')


def revoke_grant(grant, now):
    """Kill a grant: clear live token hashes (so lookups fail even if a check
    is ever missed), keep code_hash and the McpRotatedRefresh rows for replay
    detection."""
    if grant.revoked_at is None:
        grant.revoked_at = now
    grant.access_token_hash = None
    grant.refresh_token_hash = None


def _issue_tokens(now):
    """The one place token values and their hashes/expiries are computed.
    Returns the plaintext pair plus the McpGrant column values that go with
    it; callers write `columns` with a conditional UPDATE (atomic claim)."""
    access, refresh = new_token(), new_token()
    return access, refresh, {
        'access_token_hash': hash_token(access), 'access_expires_at': now + ACCESS_TTL,
        'refresh_token_hash': hash_token(refresh), 'refresh_expires_at': now + REFRESH_TTL,
    }


def _token_response(access, refresh):
    resp = jsonify(access_token=access, token_type='Bearer',
                   expires_in=int(ACCESS_TTL.total_seconds()), refresh_token=refresh, scope=SCOPE)
    resp.headers['Cache-Control'] = 'no-store'
    resp.headers['Pragma'] = 'no-cache'
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
            'authorization_response_iss_parameter_supported': True,
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
        return redirect('/oauth-consent?' + urlencode({k: v for k, v in params.items() if v is not None}))

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
                       redirect_host=redirect_host(params['redirect_uri']))

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

    def _exchange_code(f):
        now = datetime.utcnow()
        code = f.get('code') or ''
        grant = Grant.query.filter_by(code_hash=hash_token(code)).first() if code else None
        if grant is None:
            return oauth_error('invalid_grant', 'Unknown or expired authorization code')
        if grant.code_used_at is not None:
            # A replayed code revokes whatever it already issued (RFC 6749 4.1.2)
            # -- but only when the owner's client_id presents it, so a stranger
            # who merely learns a code can't kill someone's connection.
            if f.get('client_id') == grant.client_id:
                revoke_grant(grant, now)
                db.session.commit()
            return oauth_error('invalid_grant', 'Authorization code already used')
        if grant.revoked_at is not None or grant.code_expires_at is None or grant.code_expires_at < now:
            return oauth_error('invalid_grant', 'Unknown or expired authorization code')
        if f.get('client_id') != grant.client_id:
            return oauth_error('invalid_grant', 'Code was issued to another client')
        if f.get('redirect_uri') and f.get('redirect_uri') != grant.redirect_uri:
            return oauth_error('invalid_grant', 'redirect_uri mismatch')
        verifier = f.get('code_verifier') or ''
        if not _VERIFIER_RE.match(verifier) or not hmac.compare_digest(
                pkce_s256(verifier), grant.code_challenge or ''):
            return oauth_error('invalid_grant', 'PKCE verification failed')
        access, refresh, columns = _issue_tokens(now)
        # Claim the code atomically: of two concurrent requests with the same
        # code only the one whose UPDATE flips code_used_at from NULL wins.
        claimed = Grant.query.filter(Grant.id == grant.id, Grant.code_used_at.is_(None)).update(
            {'code_used_at': now, **columns}, synchronize_session=False)
        if claimed != 1:
            db.session.rollback()
            return oauth_error('invalid_grant', 'Authorization code already used')
        db.session.commit()
        return _token_response(access, refresh)

    def _refresh(f):
        now = datetime.utcnow()
        presented = f.get('refresh_token') or ''
        h = hash_token(presented) if presented else None
        grant = Grant.query.filter_by(refresh_token_hash=h).first() if h else None
        if grant is None:
            rotated = appmod.McpRotatedRefresh.query.filter_by(token_hash=h).first() if h else None
            if rotated is not None:
                # A rotated-out refresh token (any generation) came back: stolen.
                stale = db.session.get(Grant, rotated.grant_id)
                if stale is not None:
                    revoke_grant(stale, now)
                    db.session.commit()
            return oauth_error('invalid_grant', 'Unknown refresh token')
        if (grant.revoked_at is not None or grant.refresh_expires_at is None
                or grant.refresh_expires_at < now
                or (f.get('client_id') and f.get('client_id') != grant.client_id)):
            return oauth_error('invalid_grant', 'Refresh token expired or revoked')
        user = db.session.get(appmod.User, grant.user_id)
        if user is None or user.tenant_id != grant.tenant_id:
            revoke_grant(grant, now)
            db.session.commit()
            return oauth_error('invalid_grant', 'This connection is no longer valid')
        # Suspension and a switched-off module are reversible, so refuse
        # without revoking: the connection works again once they come back.
        tenant = db.session.get(appmod.Tenant, grant.tenant_id)
        if tenant is None or tenant.status != 'active' or not modules.is_enabled(tenant, 'ai_connector'):
            return oauth_error('invalid_grant', "This business's connector is not active")
        access, refresh, columns = _issue_tokens(now)
        # Claim the rotation atomically: only the request that still sees the
        # presented hash as current gets to replace it.
        claimed = Grant.query.filter(
            Grant.id == grant.id, Grant.refresh_token_hash == h, Grant.revoked_at.is_(None)
        ).update(columns, synchronize_session=False)
        if claimed != 1:
            db.session.rollback()
            return oauth_error('invalid_grant', 'Refresh token already used')
        db.session.add(appmod.McpRotatedRefresh(
            tenant_id=grant.tenant_id, grant_id=grant.id, token_hash=h))
        db.session.commit()
        return _token_response(access, refresh)

    @app.route('/oauth/token', methods=['POST'])
    @appmod.limiter.limit('60/minute')
    def mcp_oauth_token():
        f = request.form if request.form else (request.get_json(silent=True) or {})
        grant_type = f.get('grant_type')
        if grant_type == 'authorization_code':
            return _exchange_code(f)
        if grant_type == 'refresh_token':
            return _refresh(f)
        return oauth_error('unsupported_grant_type', 'Use authorization_code or refresh_token')

    @app.route('/oauth/revoke', methods=['POST'])
    def mcp_oauth_revoke():
        f = request.form if request.form else (request.get_json(silent=True) or {})
        token = f.get('token') or ''
        if token:
            h = hash_token(token)
            grant = Grant.query.filter(db.or_(Grant.access_token_hash == h,
                                              Grant.refresh_token_hash == h)).first()
            if grant is not None:
                revoke_grant(grant, datetime.utcnow())
                db.session.commit()
        return ('', 200)

    def _active_grants_query():
        now = datetime.utcnow()
        return tenant_query(Grant).filter(Grant.revoked_at.is_(None),
                                          Grant.refresh_token_hash.isnot(None),
                                          Grant.refresh_expires_at > now)

    def _me():
        return appmod.User.query.filter_by(username=get_jwt_identity()).first()

    @app.route('/api/mcp/grants', methods=['GET'])
    @jwt_required()
    def mcp_list_grants():
        me = _me()
        is_admin = 'admin' in appmod._jwt_roles()
        q = _active_grants_query()
        if not is_admin:
            q = q.filter(Grant.user_id == (me.id if me else -1))
        grants = q.order_by(Grant.created_at.desc()).all()
        names = {c.client_id: c.client_name for c in Client.query.filter(
            Client.client_id.in_({g.client_id for g in grants})).all()} if grants else {}
        return jsonify(mcp_url=origin() + '/mcp',
                       enabled=modules.is_enabled(current_tenant(), 'ai_connector'),
                       is_admin=is_admin,
                       grants=[g.to_dict(names.get(g.client_id)) for g in grants])

    @app.route('/api/mcp/grants/<int:grant_id>', methods=['DELETE'])
    @jwt_required()
    def mcp_revoke_grant(grant_id):
        me = _me()
        q = tenant_query(Grant).filter(Grant.id == grant_id, Grant.revoked_at.is_(None))
        if 'admin' not in appmod._jwt_roles():
            q = q.filter(Grant.user_id == (me.id if me else -1))
        grant = q.first()
        if grant is None:
            return jsonify(msg='Connection not found'), 404
        revoke_grant(grant, datetime.utcnow())
        db.session.commit()
        return jsonify(ok=True)
