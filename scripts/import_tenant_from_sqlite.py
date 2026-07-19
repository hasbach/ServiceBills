"""One-shot import of ONE local tenant's data into the live (Postgres) DB.

Copies every row belonging to a single tenant in a local SQLite DB into the target
database as a BRAND-NEW tenant, remapping all primary keys and foreign keys so it
never collides with existing production data. Other tenants are untouched.

Usage (run locally; needs the SQLite file + network access to the target DB):

    # PowerShell
    $env:SQLITE_PATH="instance\\database.db"
    $env:DATABASE_URL="<your Supabase session-pooler URL>"
    $env:SOURCE_TENANT_ID="1"                 # the tenant in the SQLite file to import
    $env:IMPORT_BUSINESS_NAME="My ISP"        # optional; defaults to the source business name
    python scripts\\import_tenant_from_sqlite.py

Safe to run ONCE. It refuses to run if a tenant with the derived slug already exists
(so a second run won't duplicate). Wrapped in a single transaction — all or nothing.
Imported users keep their password hashes (their logins work); a username that already
exists in the target is suffixed with the new slug.
"""
import os
import re
import sys
from datetime import datetime, timezone
from sqlalchemy import create_engine, MetaData, select, insert, inspect, Boolean


def norm(url):
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg2://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


def main():
    src_path = os.environ.get("SQLITE_PATH", "instance/database.db")
    target = os.environ.get("DATABASE_URL")
    src_tid = int(os.environ.get("SOURCE_TENANT_ID", "1"))
    name_override = os.environ.get("IMPORT_BUSINESS_NAME")
    if not target or target.startswith("sqlite"):
        # allow sqlite target only for local testing via ALLOW_SQLITE_TARGET
        if not os.environ.get("ALLOW_SQLITE_TARGET"):
            sys.exit("Set DATABASE_URL to the target Postgres URL.")

    src = create_engine(f"sqlite:///{src_path}")
    dst = create_engine(norm(target))
    smd, dmd = MetaData(), MetaData()
    smd.reflect(bind=src)
    dmd.reflect(bind=dst)
    insp = inspect(dst)

    # FK map from the TARGET schema: {table: {local_col: referred_table}}
    fkmap = {}
    for tname in dmd.tables:
        cols = {}
        for fk in insp.get_foreign_keys(tname):
            for c in fk["constrained_columns"]:
                cols[c] = fk["referred_table"]
        fkmap[tname] = cols

    # Tenant-owned domain tables = have tenant_id, excluding 'tenant' and 'user'.
    domain = [t for t, tbl in dmd.tables.items()
              if "tenant_id" in tbl.c and t not in ("tenant", "user")]

    # Topologically order domain tables so referenced tables are inserted first.
    ordered, seen = [], set()

    def visit(t):
        if t in seen:
            return
        seen.add(t)
        for col, ref in fkmap.get(t, {}).items():
            if ref in domain and ref != t:
                visit(ref)
        ordered.append(t)

    for t in domain:
        visit(t)

    # A legacy (single-tenant desktop) source has no tenant_id/tenant table — import
    # ALL its rows as the new tenant. A migrated source is filtered by SOURCE_TENANT_ID.
    src_has_tenant = "customer" in smd.tables and "tenant_id" in smd.tables["customer"].c
    print("Source type:", "migrated (filtering tenant %d)" % src_tid if src_has_tenant
          else "legacy single-tenant (importing all rows)")

    def src_rows(tname):
        st = smd.tables[tname]
        q = select(st)
        if src_has_tenant and "tenant_id" in st.c:
            q = q.where(st.c.tenant_id == src_tid)
        return sc.execute(q)

    with src.connect() as sc, dst.begin() as dc:
        # Derive business name + a unique slug; refuse if it already exists.
        biz_name = name_override
        if not biz_name and "business_settings" in smd.tables:
            row = next(iter(src_rows("business_settings")), None)
            biz_name = getattr(row, "business_name", None) if row else None
        biz_name = biz_name or "Imported Business"
        base = re.sub(r"[^a-z0-9]+", "-", biz_name.lower()).strip("-")[:70] or "imported"
        slug, i = base, 1
        tenant_t = dmd.tables["tenant"]
        while dc.execute(select(tenant_t).where(tenant_t.c.slug == slug)).first():
            i += 1
            slug = f"{base}-{i}"
            if i > 1:
                sys.exit(f"A tenant with slug '{base}' already exists — refusing to re-import.")

        new_tid = dc.execute(insert(tenant_t).values(
            name=biz_name, slug=slug, status="active", plan="free",
            created_at=datetime.now(timezone.utc))).inserted_primary_key[0]
        print(f"Created target tenant '{biz_name}' (slug={slug}, id={new_tid})")

        idmap = {}  # {table: {old_id: new_id}}

        def copy_table(tname):
            dt = dmd.tables[tname]
            dcols = set(dt.c.keys())
            bool_cols = {c.name for c in dt.c if isinstance(c.type, Boolean)}
            notnull = {c.name for c in dt.c if not c.nullable}
            m, skipped = {}, 0
            for row in src_rows(tname):
                d = {k: v for k, v in dict(row._mapping).items() if k in dcols}
                old_id = d.pop("id", None)
                d["tenant_id"] = new_tid
                skip = False
                for col, ref in fkmap.get(tname, {}).items():
                    if col == "tenant_id" or col not in d or d[col] is None:
                        continue
                    if ref == "tenant":
                        d[col] = new_tid
                        continue
                    mapped = idmap.get(ref, {}).get(d[col])
                    if mapped is None and col in notnull:
                        skip = True  # required reference not imported (orphaned legacy row)
                        break
                    d[col] = mapped
                if skip:
                    skipped += 1
                    continue
                for bc in bool_cols:
                    if d.get(bc) is not None:
                        d[bc] = bool(d[bc])
                new_id = dc.execute(insert(dt).values(**d)).inserted_primary_key[0]
                if old_id is not None:
                    m[old_id] = new_id
            idmap[tname] = m
            print(f"  {tname}: {len(m)}" + (f"  (skipped {skipped} orphaned)" if skipped else ""))

        # Users first (so payments/tickets can remap collected_by, etc.).
        dt = dmd.tables["user"]
        dcols = set(dt.c.keys())
        idmap["user"] = {}
        for row in src_rows("user"):
            d = {k: v for k, v in dict(row._mapping).items() if k in dcols}
            old_id = d.pop("id", None)
            d["tenant_id"] = new_tid
            if dc.execute(select(dt).where(dt.c.username == d.get("username"))).first():
                d["username"] = f"{d['username']}_{slug}"
            d.setdefault("email_verified", True)
            new_id = dc.execute(insert(dt).values(**d)).inserted_primary_key[0]
            idmap["user"][old_id] = new_id
        print(f"  user: {len(idmap['user'])}")

        for tname in ordered:
            if tname in smd.tables:
                copy_table(tname)
            else:
                idmap[tname] = {}   # table doesn't exist in a legacy source — nothing to import
                print(f"  {tname}: (not in source, skipped)")

    print(f"\nDone. Imported source tenant {src_tid} as new tenant id {new_tid} ('{slug}').")


if __name__ == "__main__":
    main()
