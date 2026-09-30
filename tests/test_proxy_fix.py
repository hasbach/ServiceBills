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
