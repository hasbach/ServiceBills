"""One-shot data migration: SQLite (instance/database.db) -> PostgreSQL.

Prereqs:
  1. Provision Postgres (Supabase) and build the schema first:
       DISABLE_AUTO_CREATE_ALL=1 DATABASE_URL=<pg> JWT_SECRET_KEY=<s> flask db upgrade
  2. Run this script:
       SQLITE_PATH=instance/database.db DATABASE_URL=<pg> python scripts/migrate_sqlite_to_postgres.py

Copies every table preserving primary keys and FK order, coerces SQLite 0/1 to
Postgres booleans, then resets each table's id sequence. Idempotency is NOT
guaranteed — run once against an empty (freshly-migrated) Postgres schema.

Note: WhatsApp access_token/app_secret copy as-is (plaintext from the old DB);
the app's EncryptedString decrypts plaintext gracefully and re-encrypts on next
save. Re-save WhatsApp settings post-migration to encrypt them at rest.
"""
import os
import sys
from sqlalchemy import create_engine, MetaData, select, insert, text, Boolean

# FK dependency order: parents before children.
ORDER = [
    "tenant", "user", "subscription_plan", "reseller", "supplier", "sector",
    "expense_category", "customer", "payment", "reseller_payment",
    "supplier_payment", "expense", "generated_receipt", "addon_purchase",
    "business_settings", "whats_app_settings", "system_update_settings",
    "service_status", "support_ticket", "ticket_log", "push_subscription",
    "service_outage", "customer_feedback", "payment_reminder",
]


def main():
    sqlite_path = os.environ.get("SQLITE_PATH", "instance/database.db")
    dst_url = os.environ.get("DATABASE_URL")
    if not dst_url or dst_url.startswith("sqlite"):
        sys.exit("Set DATABASE_URL to the target Postgres URL.")

    src = create_engine(f"sqlite:///{sqlite_path}")
    dst = create_engine(dst_url)
    src_md, dst_md = MetaData(), MetaData()
    src_md.reflect(bind=src)
    dst_md.reflect(bind=dst)

    with src.connect() as sc, dst.begin() as dc:
        for name in ORDER:
            if name not in src_md.tables or name not in dst_md.tables:
                print(f"skip {name} (missing in src or dst)")
                continue
            st, dt = src_md.tables[name], dst_md.tables[name]
            bool_cols = {c.name for c in dt.c if isinstance(c.type, Boolean)}
            rows = []
            for r in sc.execute(select(st)):
                d = dict(r._mapping)
                for bc in bool_cols:
                    if d.get(bc) is not None:
                        d[bc] = bool(d[bc])
                rows.append(d)
            if rows:
                dc.execute(insert(dt), rows)
            print(f"{name}: {len(rows)} rows")

        # Reset id sequences so future inserts don't collide with copied PKs.
        for name in ORDER:
            if name in dst_md.tables and "id" in dst_md.tables[name].c:
                dc.execute(text(
                    f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), "
                    f"COALESCE((SELECT MAX(id) FROM \"{name}\"), 1))"
                ))
    print("Migration complete.")


if __name__ == "__main__":
    main()
