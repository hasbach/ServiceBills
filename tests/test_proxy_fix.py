import importlib
import subprocess
import sys


def test_no_proxyfix_without_trust_proxy():
    import app as appmod
    from werkzeug.middleware.proxy_fix import ProxyFix
    assert not isinstance(appmod.app.wsgi_app, ProxyFix)


def test_proxyfix_enabled_with_trust_proxy():
    code = ("import os; os.environ['TRUST_PROXY']='1'; os.environ['DATABASE_PATH']=':memory:'; "
            "os.environ['JWT_SECRET_KEY']='x'; import app; "
            "from werkzeug.middleware.proxy_fix import ProxyFix; "
            "print(isinstance(app.app.wsgi_app, ProxyFix))")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("True"), out.stderr[-500:]


def test_trust_proxy_hop_count_picks_real_client_ip():
    # Cloudflare Tunnel + Traefik: "client, cloudflared" -> TRUST_PROXY=2 must yield the client.
    code = ("import os; os.environ['TRUST_PROXY']='2'; os.environ['DATABASE_PATH']=':memory:'; "
            "os.environ['JWT_SECRET_KEY']='x'; import app; "
            "env = {'REQUEST_METHOD': 'GET', 'PATH_INFO': '/', 'SERVER_NAME': 'x', 'SERVER_PORT': '80', "
            "'wsgi.url_scheme': 'http', 'REMOTE_ADDR': '10.0.1.5', "
            "'HTTP_X_FORWARDED_FOR': '6.6.6.6, 185.97.94.99, 10.0.1.1', 'HTTP_X_FORWARDED_PROTO': 'https'}; "
            "seen = {}; "
            "app.app.wsgi_app.app = lambda e, s: (seen.update(ip=e['REMOTE_ADDR'], scheme=e['wsgi.url_scheme']), [])[1]; "
            "app.app.wsgi_app(env, lambda *a: None); print(seen['ip'], seen['scheme'])")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120)
    assert out.stdout.strip().endswith("185.97.94.99 https"), out.stderr[-500:]
