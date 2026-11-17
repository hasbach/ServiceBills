"""Books (roadmap 1C): ledger tables."""
from datetime import date

import app as appmod
from tests.conftest import make_tenant
from tests.test_superadmin import _superadmin_headers


def test_ledger_tables_exist(app, client):
    make_tenant(client, "Books Biz", "books_admin")
    sa = _superadmin_headers(app, client)
    with app.app_context():
        tid = appmod.Tenant.query.filter_by(slug="books-biz").first().id
        acct = appmod.LedgerAccount(tenant_id=tid, key="cash", number=1000,
                                    name="Cash", type="asset")
        entry = appmod.JournalEntry(tenant_id=tid, entry_date=date(2026, 11, 17),
                                    kind="event", memo="test")
        appmod.db.session.add_all([acct, entry])
        appmod.db.session.flush()
        line = appmod.JournalLine(tenant_id=tid, entry_id=entry.id, account_id=acct.id,
                                  debit=10, credit=0, entry_date=entry.entry_date,
                                  source_type="payment", source_id=1)
        appmod.db.session.add(line)
        appmod.db.session.commit()

        assert appmod.LedgerAccount.query.filter_by(tenant_id=tid, key="cash").one().number == 1000
        assert appmod.JournalEntry.query.filter_by(tenant_id=tid).one().kind == "event"
        assert appmod.JournalLine.query.filter_by(tenant_id=tid).one().debit == 10

    assert client.delete(f"/api/admin/tenants/{tid}", headers=sa).status_code == 200
    with app.app_context():
        assert appmod.LedgerAccount.query.filter_by(tenant_id=tid).count() == 0
        assert appmod.JournalEntry.query.filter_by(tenant_id=tid).count() == 0
        assert appmod.JournalLine.query.filter_by(tenant_id=tid).count() == 0
