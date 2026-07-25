from unittest.mock import patch
import app as appmod


def test_month_key_uses_to_char_when_dialect_is_postgresql(app):
    with app.app_context():
        with patch.object(appmod.db.engine.dialect, 'name', 'postgresql'):
            expr = appmod.month_key(appmod.Payment.date)
        compiled = str(expr).lower()
        assert 'to_char' in compiled
        assert 'strftime' not in compiled


def test_month_key_uses_strftime_when_dialect_is_sqlite(app):
    with app.app_context():
        with patch.object(appmod.db.engine.dialect, 'name', 'sqlite'):
            expr = appmod.month_key(appmod.Payment.date)
        compiled = str(expr).lower()
        assert 'strftime' in compiled
        assert 'to_char' not in compiled
