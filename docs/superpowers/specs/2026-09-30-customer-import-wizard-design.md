# Customer Import Wizard — Design

**Date:** 2026-09-30
**Status:** Approved (brainstorming session)

## Goal

A new tenant moving to ServiceBills from spreadsheets or another system can
load all existing customers and their current subscription state in one go,
from an Excel template, instead of typing them one by one through Add Customer.

## Decisions (made with the owner)

| Question | Decision |
|---|---|
| Financial / WhatsApp behaviour | **Migration mode**: expiry is taken as-is, no back-billing, no Whish links, no WhatsApp. Optional `opening_balance` records what the customer already owes. |
| File format | **.xlsx only** (openpyxl). Template is generated per tenant with dropdowns. |
| Unknown plan in sheet | **Offer to create it** in the wizard (admin enters price / cycle / currency). Unknown sectors auto-created. Unknown reseller / upstream provider / network device = row error. |
| Rows with errors | **Import valid rows, skip bad ones**; skipped rows downloadable as an .xlsx error report. |
| Duplicates | Row whose (name, phone) already exists in the tenant, or repeats an earlier row in the sheet, = warning, skipped. Makes re-uploading a fixed file safe. |
| Customer cap | `plans.limits(tenant.plan)["max_customers"]` enforced; rows past the cap = error. |

## The billing-anchor rule (critical)

`generate_missing_payments` (app.py ~3020) resumes billing for a
non-reseller customer from their latest non-prepayment `Payment.date`, or
from `subscription_start_date` if they have none, stepping one cycle at a
time up to now. For reseller customers it resumes from
`subscription_expiry_date − 1 cycle`.

An imported customer has no payment history, so if we stored their real
"customer since" date the next scheduler run would back-bill every cycle
since then. Therefore the import stores:

- `subscription_start_date = expiry_date − 1 billing cycle` (the start of the
  current, already-paid period), and
- the opening-balance `Payment` (if any) is dated at that same anchor.

Result: the scheduler's next charge lands exactly on `expiry_date`, the same
invariant `add_customer` produces. There is therefore **no start_date column**
in the template. A customer whose expiry is already in the past and who is
`active=yes` will be billed from their expiry on the next scheduler run —
the normal overdue behaviour — and the preview flags such rows as a warning.

## Template (`GET /api/customers/import/template`)

Workbook with three sheets:

1. **Customers** — header row (bold, frozen), columns in this order:

| Column | Req | Notes |
|---|---|---|
| name | ✔ | ≤100 chars |
| phone | ✔ | text-formatted column (keeps leading zeros), ≤20 chars |
| address | ✔ | ≤200 chars |
| plan | ✔ | dropdown of tenant plan names; any other name → "new plan" |
| expiry_date | ✔ | date cell or `YYYY-MM-DD` text |
| active | | `yes`/`no`, default yes |
| sector | | dropdown of sectors; unknown → auto-created |
| reseller | | dropdown of reseller names; unknown → error |
| discount | | number ≥0, default 0 |
| cost_override | | number ≥0 |
| opening_balance | | number ≥0 = amount customer currently owes |
| upstream_provider | | dropdown of upstream provider names; unknown → error |
| upstream_username | | |
| network_device | | dropdown of network device names; unknown → error |
| pppoe_username | | |
| onu_mac | | MAC |
| cpe_mac | | MAC, unique per tenant |
| notes | | |
| whatsapp_enabled | | `yes`/`no`, default yes |

2. **Instructions** — one line per column explaining it, plus the migration
   rules (no billing on import, opening balance meaning, expiry = paid-through).
3. **Lists** — hidden sheet holding the dropdown source lists. Dropdowns use
   data validation with `showErrorMessage=False` on `plan` and `sector` (new
   values allowed) and `True` on reseller / upstream / device.

## Validation (`POST /api/customers/import/validate`, multipart `file`)

Returns:

```json
{
  "rows": [{"row": 2, "status": "ok|warning|error", "messages": ["..."],
            "data": {"name": "...", "phone": "...", "plan": "...", "expiry_date": "2026-10-15", ...}}],
  "summary": {"total": 120, "ok": 110, "warning": 6, "error": 4,
              "unknown_plans": ["Fiber 50M"], "new_sectors": ["Hamra"],
              "customer_limit": 50, "existing_customers": 0}
}
```

Rules per row (all messages collected; a row is `error` if any error,
`warning` if only warnings):

- required fields present; length limits.
- `expiry_date` parses (Excel date cell, datetime, or `YYYY-MM-DD` / `DD/MM/YYYY` text).
- `discount`, `cost_override`, `opening_balance` finite and ≥0.
- `active`, `whatsapp_enabled` in yes/no/true/false/1/0 (case-insensitive), blank = default.
- `plan`: matched case-insensitively (trimmed) to a tenant plan; unmatched →
  listed in `unknown_plans`; the row is `error` at commit time only if no
  definition was supplied for it (at validate time it's a warning "new plan").
- `reseller` / `upstream_provider` / `network_device`: case-insensitive match
  on name within tenant; none or more than one match → error.
- `onu_mac`, `cpe_mac` via `_validate_mac_address(raw, allow_empty=True)`.
- `cpe_mac` via `_check_cpe_mac_available`; also unique within the sheet.
- `(network_device, pppoe_username)` and `(upstream_provider, upstream_username)`
  via `_check_network_link_conflict(None, ...)`; also unique within the sheet.
- `notes` via `_clean_customer_notes`.
- duplicate (name, phone) in DB or earlier in sheet → warning "duplicate — will be skipped".
- expiry in the past and active → warning "expired — will be billed on next billing run".
- customer cap: after counting existing customers, importable rows beyond the
  cap → error "customer limit reached".

File-level 400s: not an .xlsx / unreadable, no `Customers` sheet, header row
missing a required column, zero data rows, more than 5,000 data rows, file
> 5 MB. Unknown extra columns are ignored.

## Commit (`POST /api/customers/import/commit`)

Multipart: `file` (the same workbook) + `new_plans` (JSON string):
`[{"name": "Fiber 50M", "price": 25, "billing_cycle": "monthly", "currency": "USD", "cost": 0}]`.

Server re-runs validation with the plan definitions (never trusts client
rows), then in **one transaction**:

1. Creates the new plans (`status='active'`); validates price ≥0, cycle in
   monthly/yearly, currency exists in `Currency`.
2. Creates missing `Sector` rows.
3. For every row with status ok (warnings about "expired" still import;
   duplicate and error rows are skipped):
   - `Customer` via `new_for_tenant`, `subscription_expiry_date = expiry`,
     `subscription_start_date = expiry − 1 cycle`, `is_subscription_active`
     from `active`, `balance = 0`.
   - if `opening_balance > 0`:
     - reseller customer → `reseller.balance += amount` and a
       `ResellerPayment(type='credit_added', date=anchor, description='Opening balance (import)')`.
     - otherwise → unpaid `Payment(amount, paid=False, pre_payment=False, date=anchor)`
       and `customer.balance -= amount`. **No** `_maybe_create_customer_payment_link`.
4. `db.session.commit()`, then `recalculate_estimated_profit(tenant_id)` once.

Any exception → rollback, 400 with message; nothing is written.

Response: `{"imported": 110, "skipped": 10, "plans_created": 1, "sectors_created": 1,
"skipped_rows": [{"row": 7, "messages": [...]}]}`.

## Error report

Built client-side is not possible without an xlsx lib, so:
`POST /api/customers/import/error_report` (multipart `file` + `new_plans`)
re-validates and returns an .xlsx with only the non-imported rows, in the
template's column order, plus a final `errors` column. Frontend downloads it
as a blob.

## Auth & limits

All four endpoints: `@jwt_required()` + `admin_required()`. Template is
tenant-scoped via `tenant_query`. Request size guard 5 MB on uploads.

## Code layout

- `customer_import.py` — new module, pure logic + route registration,
  following the `whatsapp_inbox_routes.register_inbox_routes(app, appmod)`
  pattern (registered from app.py with `sys.modules[__name__]`), so app.py
  only gains two lines. Functions: `build_template(appmod)`,
  `read_workbook(file_storage)`, `validate_rows(appmod, raw_rows, new_plans)`,
  `commit_import(appmod, validated, new_plans)`, `build_error_report(...)`,
  `register_customer_import_routes(app, appmod)`.
- `requirements.txt` — add `openpyxl`.
- `frontend/src/components/CustomerImportWizard.js` — MUI Dialog + Stepper
  (pattern of SetupWizardView.js): 1 Download template · 2 Upload · 3 Review
  (status filter chips, row table, new-plan form) · 4 Done (summary + error
  report download).
- `frontend/src/context/AppContext.js` — four `apiService` methods.
- `frontend/src/components/SubscriptionsView.js` — "Import" button next to
  Export, admin only, opens the wizard, refreshes the list on success.

## Testing

`tests/test_customer_import.py` (pytest, in-memory SQLite, `make_tenant`):
template downloads and lists tenant plans; round-trip (build template → fill
with openpyxl in-test → validate → commit); each validation rule; new-plan
creation; sector auto-create; opening balance for normal and reseller
customers; duplicate skip on re-upload; plan cap; tenant isolation (tenant B
can't see A's names in template, A's reseller name is an error for B);
non-admin gets 403; **`generate_missing_payments(tenant_id)` right after
commit creates no payments and leaves expiry unchanged for future-expiry
customers**; nothing written when commit fails mid-way. No WhatsApp send
happens (monkeypatch `send_whatsapp_message` to fail the test if called).

Frontend: one Jest render test that the wizard opens on step 1.

## Out of scope

CSV upload, updating existing customers via import, importing payment
history, RADIUS/upstream sync on import, background jobs.
