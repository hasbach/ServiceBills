# Books (double-entry ledger) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every money event in ServiceBills writes a balanced, permanent journal entry in the same transaction. Stored balances are checked against the books every night. Admins get a read-only Finance → Books tab.

**Architecture:** A new pure module `ledger.py` maps each financial record, in its current state, to the journal lines it *should* contribute. An `after_flush` listener in `app.py` posts *desired − already posted* (starting-point lines + earlier lines for that record), next to the existing History (audit log) listener. A `before_commit` safety net posts any stored-balance change that no line explains as `unexplained`. A nightly job starts the books per business (cut-over) and runs the check.

**Tech Stack:** Flask, Flask-SQLAlchemy, Alembic (Flask-Migrate), pytest (SQLite; Postgres via `DATABASE_URL`), React + MUI (CRA, Jest).

**Spec:** `docs/superpowers/specs/2026-11-17-ledger-design.md` (read it first; this plan argues from it).

## Global Constraints

- **USD only:** ignore `currency` and `fx_rate_to_reporting`.
- **Money accounts:** Cash and Whish only.
- **Insert-only books:** journal entries and lines are never updated or deleted by any route, screen or AI tool. The only exception is tenant delete.
- **The books never block a payment.** A mapping/posting error writes a `ledger_problem` row in the same transaction and lets the user's action commit. An entry is never committed unbalanced.
- **Dates** are business-local days, via the existing helpers `_local_day(settings, dt, calendar=…)`, `_tenant_today()` and `_zone_for(settings)`.
- **Migrations:**
  - new tables only;
  - random revision id (`secrets.token_hex(6)`);
  - single head, `down_revision = 'b6e00fdf5d15'`;
  - guarded with `_has_table` like `7c41f2e9b6d5_add_audit_log.py`.
- **Tenant delete:** every new table goes into `_TENANT_DELETE_ORDER` in `app.py`, before `Payment` (children first).
- **Scheduled jobs:** a new job goes into `SCHEDULE` and gets a matching cron line in `.github/workflows/scheduled-jobs.yml` (a test enforces this).
- **Permissions:** the Books API and tab are admin only (`admin_required()`).
- **Test runs:**
  - Full backend suite: `python -m pytest -q -p no:cacheprovider tests`. It must stay green; last count 1843.
  - Frontend tests must run from a copy outside `.claude`, because Jest skips paths containing `.claude`. See memory/project notes.
- **Commit trailer:** `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Whish callbacks and the scheduler run without a JWT or request tenant.** Posting must take the tenant from the record (`record.tenant_id`), never from `current_tenant_id()`. Test in Task 3: `test_whish_callback_payment_posts_with_record_tenant`.
2. **The nightly billing run creates hundreds of bills in one transaction.** Posting must stay a bulk insert with no per-new-record lookup. Test in Task 3: `test_bulk_billing_posts_one_entry_per_date` (200 bills, one query count bound).
3. **A flush can mix tenants**, some with books started and some without. Only started tenants get lines. Test in Task 3: `test_tenant_without_books_gets_no_lines`.
4. **Revert then re-pay the same day, and switching cash ↔ Whish.** Correct net lines on the right money account. Tests in Task 3: `test_revert_then_repay_nets_to_one_receipt` and `test_method_change_moves_money_between_cash_and_whish`.
5. **Deleting a customer with open bills and credit.** Income and receipts stay; one write-off of the leftover balance. Test in Task 4: `test_customer_delete_keeps_money_and_writes_off_balance`.

---

## File Structure

| File | Responsibility |
|---|---|
| `ledger.py` (new) | Pure mapping, no DB:<ul><li>`Line` type;</li><li>chart of accounts `CHART`;</li><li>`desired_lines(kind, record, local_day)`;</li><li>`party_balance_from_books(party_type, debit_minus_credit)`;</li><li>`diff_lines(desired, already, profit_to_opening)`.</li></ul> Imports nothing from `app.py`. |
| `app.py` | <ul><li>Models (next to `AuditLog`).</li><li>Posting listener and safety net (next to `_write_audit_log`).</li><li>`start_books`, `run_books_check`.</li><li>Manual-posting hooks in the no-record paths.</li><li>`/api/books/*` routes.</li><li>Nightly job.</li></ul> |
| `migrations/versions/<id>_ledger_books.py` (new) | All new tables. |
| `.github/workflows/scheduled-jobs.yml` | The cron line for `ledger_nightly`. |
| `frontend/src/components/BooksView.js` (new) | Books tab with three sub-tabs: Accounts, Journal and Check. |
| `frontend/src/components/FinanceView.js` | Register the `books` tab (admin). |
| `frontend/src/context/AppContext.js` | The API calls `fetchBooksAccounts`, `fetchBooksJournal`, `fetchBooksCheck`. |
| `tests/test_ledger_mapping.py` | Unit tests of `ledger.py`. |
| `tests/test_ledger_posting.py` | Listener tests through the real routes. |
| `tests/test_ledger_cutover.py` | Tests for `start_books`, starting point, pre-start corrections and the no-record paths. |
| `tests/test_ledger_check.py` | Nightly check and API tests. |
| `frontend/src/components/BooksView.test.js` | Rendering of the three sub-tabs. |

---

### Task 1: Tables and migration

**Files:**
- Modify: `app.py`: models after `class AuditLog`, and `_TENANT_DELETE_ORDER`.
- Create: `migrations/versions/<id>_ledger_books.py`
- Test: `tests/test_ledger_check.py` (table smoke test)

**Interfaces — Produces (all with `tenant_id` FK `tenant.id`, NOT NULL, indexed):**
- **`LedgerAccount`:** `id`, `tenant_id`, `key` String(60), `number` Integer, `name` String(120), `type` String(10) (`asset`/`liability`/`equity`/`income`/`expense`), `party_type` String(20) nullable. Unique index on (`tenant_id`, `key`).
- **`JournalEntry`:**
  - `id`, `tenant_id`;
  - `entry_date` Date;
  - `kind` String(12): `event` / `opening` / `correction` / `unexplained`;
  - `memo` String(300);
  - `created_by` String(100) nullable;
  - `created_at` DateTime default utcnow.
- **`JournalLine`:**
  - `id`, `tenant_id`;
  - `entry_id` FK `journal_entry.id`;
  - `account_id` FK `ledger_account.id`;
  - `party_type` String(20) nullable, `party_id` Integer nullable;
  - `debit` and `credit`: Numeric(18,4, asdecimal=False), default 0;
  - `source_type` String(30) nullable, `source_id` Integer nullable;
  - `entry_date` Date, copied from the entry for indexed range queries.
  - Indexes: (`tenant_id`, `account_id`, `entry_date`), (`source_type`, `source_id`), (`tenant_id`, `party_type`, `party_id`).
- **`LedgerStartLine`:** `id`, `tenant_id`, `source_type`, `source_id`, `account_key` String(60), `party_type`, `party_id`, `amount` Numeric(18,4) signed (+debit/−credit). Index on (`source_type`, `source_id`).
- **`LedgerState`:** `tenant_id` PK FK, `started_at` DateTime, `start_day` Date, `opening_entry_id` Integer nullable, `last_check_at` DateTime nullable, `last_check` Text (JSON) nullable.
- **`LedgerProblem`:** `id`, `tenant_id`, `source_type`, `source_id` nullable, `error` Text, `created_at`.

- [ ] **Step 1: Write the failing test.** `test_ledger_tables_exist`: in the app context, insert one `LedgerAccount`, one `JournalEntry` and one `JournalLine` for a `make_tenant` tenant; commit; read them back. Then delete the tenant through the existing tenant-delete path (`DELETE /api/admin/tenants/<id>` or the helper the tenant-delete tests use) and assert all three rows are gone.
- [ ] **Step 2:** Run `python -m pytest -q -p no:cacheprovider tests/test_ledger_check.py::test_ledger_tables_exist`. Expected: FAIL, ImportError on `LedgerAccount`.
- [ ] **Step 3:** Add the six models, and add `JournalLine, JournalEntry, LedgerStartLine, LedgerProblem, LedgerAccount, LedgerState` to `_TENANT_DELETE_ORDER` before `Payment`, in that order (lines before entries before accounts). Write the migration creating the six tables and indexes. `down_revision = 'b6e00fdf5d15'`.
- [ ] **Step 4:** Run the test, then `python -m flask --app app db heads` against a scratch SQLite `DATABASE_URL`. Expected: test PASS, one head.
- [ ] **Step 5:** Commit: `feat(books): ledger tables`.

---

### Task 2: Pure mapping module `ledger.py`

**Files:**
- Create: `ledger.py`
- Test: `tests/test_ledger_mapping.py`

**Interfaces — Produces:**
- `Line = namedtuple('Line', 'account_key party_type party_id amount day')`: `amount` is signed (+ = debit, − = credit), `day` is a `date`.
- `CHART: list[tuple[key, number, name, type, party_type]]`, exactly the table in the spec's "Accounts" section. Expense categories are created on first use as key `expense:<category_id>`, number `6000 + category_id`, name = category name, type `expense`.
- `PROFIT_TYPES = {'income', 'expense'}`; `account_type(key) -> str`, where any key starting with `expense:` is `expense`.
- `desired_lines(kind: str, rec, local_day: Callable[[datetime, bool], date]) -> list[Line]`:
  - `kind` is one of: `payment`, `reseller_payment`, `upstream_payment`, `supplier_payment`, `expense`, `salary_charge`, `salary_payment`, `cash_entry`, `account_transfer`, `day_close`.
  - `local_day(dt, calendar)` returns the business-local date.
  - Returns `[]` for a deleted record. The caller passes `deleted=True` via the `rec` wrapper: `rec` is any object with the model's attributes plus `_deleted: bool`.
- `money_account(via) -> 'cash' | 'whish'`: `whish` for `'whish'` or `'whish_transfer'`, else `cash`.
- `diff_lines(desired: list[Line], already: list[Line], today: date, profit_to_opening: bool) -> list[Line]`:
  - Nets both lists by (`account_key`, `party_type`, `party_id`).
  - Returns desired minus already, dropping |net| < 0.00005.
  - **Line day:** a positive-delta line (the line exists in desired and grew) keeps the desired line's `day`. A shrinking or removed line gets `today`.
  - **Profit to opening:** when `profit_to_opening` is True, a line whose account is a profit type *and* also appears in `already` goes to `'opening'` instead (prior-period adjustment).
- `party_balance_from_books(party_type: str, debit_minus_credit: float) -> float`:
  - `customer`: `−x`;
  - `reseller`, `upstream`: `x`;
  - `supplier`, `employee`: `−x`.
- `balance_delta_to_lines(party_type, party_id, delta_balance, day) -> list[Line]`: two lines, the party account and `'corrections'`, signed so that `party_balance_from_books` of the party line equals `delta_balance`.

Mapping rules: exactly the spec's "What each record should contribute" section. Use `local_day(payment.date, True)` for bill lines and `local_day(collected_at or paid_at, False)` for receipt and refund lines. Other models use their own date column (`date`, `payment_date` or `day`).

- [ ] **Step 1: Write the failing tests**, one per rule, each using `types.SimpleNamespace` records and asserting the exact `Line` list and that `sum(amount) == 0`:
  - **Bills:** `test_unpaid_bill`, `test_paid_bill_cash`, `test_paid_bill_whish_transfer`, `test_bill_settled_from_credit_has_no_money_line`, `test_gratis_bill`.
  - **Customer money rows:** `test_prepayment`, `test_refund`, `test_reverted_payment_keeps_only_bill_lines`, `test_deleted_record_has_no_lines`.
  - **Reseller rows:** `test_reseller_credit_added`, `test_reseller_discount`, `test_reseller_payment_received_whish`, `test_reseller_payment_collected_legacy`.
  - **Upstream rows:** `test_upstream_topup`, `test_upstream_renewal_cost`.
  - **Suppliers and expenses:** `test_supplier_payment`, `test_expense_on_supplier_credit`, `test_expense_payroll_payment`, `test_plain_expense_category_account`.
  - **Payroll:** `test_salary_charge`, `test_salary_deduction`, `test_salary_payment_legacy_has_no_lines`.
  - **Cash and day close:** `test_cash_entry`, `test_account_transfer`, `test_day_close_shortfall_and_surplus`.
  - **`diff_lines`:** `test_diff_new_record_keeps_natural_days`, `test_diff_removal_dated_today`, `test_diff_profit_to_opening_only_for_accounts_already_posted`.
  - **`party_balance_from_books`:** `test_party_balance_signs` (all five party types).
- [ ] **Step 2:** Run `python -m pytest -q -p no:cacheprovider tests/test_ledger_mapping.py`. Expected: FAIL, `ModuleNotFoundError: ledger`.
- [ ] **Step 3:** Implement `ledger.py`.
- [ ] **Step 4:** Run the test file again. Expected: all PASS.
- [ ] **Step 5:** Commit: `feat(books): pure mapping of financial records to journal lines`.

---

### Task 3: Posting listener

**Files:**
- Modify: `app.py`. Add the listener after `_write_audit_log`; add the capture listener *before* `_balance_changes_as_increments` (registration order matters).
- Test: `tests/test_ledger_posting.py`

**Interfaces:**
- **Consumes:** Task 1 models; Task 2 `desired_lines`, `diff_lines`, `Line`, `CHART`.
- **Produces:**
  - **`_LEDGER_MODELS`:** dict model → kind, covering `Payment`, `ResellerPayment`, `UpstreamProviderPayment`, `SupplierPayment`, `Expense`, `SalaryCharge`, `SalaryPayment`, `CashEntry`, `AccountTransfer` and `DayClose`.
  - **`_books_started(tenant_id) -> LedgerState | None`:** cached in `session.info['ledger_started']` per transaction.
  - **`_ledger_account_id(tenant_id, key, name_hint=None) -> int`:** creates `expense:<id>` accounts on first use; caches per transaction.
  - **`_post_lines(tenant_id, lines: list[Line], kind: str, memo: str, source=(type, id) | None)`:** groups lines by `day`, writes one `JournalEntry` per day plus its `JournalLine`s, using `session.execute(insert)` like `_write_audit_log`, and adds the per-party totals to `session.info['ledger_party_posted']`. Raises `ValueError` if a day's lines do not sum to 0.
  - **`_post_ledger(session, flush_context)`:** an `after_flush` listener. For each new/dirty/deleted object of a mapped model:
    - Skip unless `_books_started(obj.tenant_id)`.
    - Compute `desired` (empty if deleted).
    - Compute `already` = `LedgerStartLine` + posted `JournalLine` for (`source_type`, `id`). New objects skip the query.
    - `profit_to_opening` = any start lines exist.
    - Post `diff_lines(...)` with kind `event` for new objects and `correction` otherwise, `source = (kind, id)`.
    - Wrap each object in try/except. On error: log, insert a `LedgerProblem`, continue.
  - The memo for an event is built from the record: e.g. `"Bill: <reason>"`, `"Payment received"`, `"Expense: <description>"`.

**Implementation notes:**
- **Line day:** `local_day` uses `_local_day(settings, dt, calendar)`, with settings = the record tenant's `BusinessSettings`, cached per transaction.
- **Customer id lookup:** the customer id for the party comes from `payment.customer_id`. Its tenant comes from `payment.tenant_id`.

- [ ] **Step 1: Write the failing tests**, through the real HTTP routes, with books started via `start_books` (Task 5). Until then, use a test helper that inserts `LedgerState` and creates the chart:
  - `test_new_bill_posts_receivable_and_income`
  - `test_marking_paid_posts_cash_receipt`
  - `test_revert_then_repay_nets_to_one_receipt`
  - `test_method_change_moves_money_between_cash_and_whish` (`set_payment_method` route)
  - `test_partial_payment_split_balances`
  - `test_bill_settled_from_credit_posts_no_money`
  - `test_refund_posts_money_out`
  - `test_delete_unpaid_bill_reverses_it_dated_today`
  - `test_expense_supplier_credit_and_payment`
  - `test_payroll_charge_and_payment`
  - `test_reseller_charge_collection_discount`
  - `test_upstream_topup_and_correction` (edit via `PUT /api/upstream-providers/<id>/entries/<eid>`)
  - `test_cash_entry_transfer_and_day_close`
  - `test_whish_callback_payment_posts_with_record_tenant` (call `_mark_payment_fully_paid` inside a bare `app_context` with no JWT)
  - `test_bulk_billing_posts_one_entry_per_date`: run `generate_missing_payments` for 200 customers, assert one entry per bill date, and assert, with an SQLAlchemy `before_cursor_execute` counter, that SELECTs on `journal_line` stay ≤ 5.
  - `test_tenant_without_books_gets_no_lines`
  - `test_posting_error_does_not_block_payment`: monkeypatch `ledger.desired_lines` to raise. The route still returns 200, the payment is saved, and one `LedgerProblem` row exists.

  **Every test also asserts:**
  - the sum of debits equals the sum of credits over all lines;
  - `party_balance_from_books(customer, Σ customer lines) == customer.balance − opening part` (helper `_books_party_balance`).
- [ ] **Step 2:** Run `python -m pytest -q -p no:cacheprovider tests/test_ledger_posting.py`. Expected: FAIL.
- [ ] **Step 3:** Implement the listener and helpers above.
- [ ] **Step 4:** Run the file, then the full suite. Expected: all PASS, and the full suite still green (books not started for existing tests, so there are no lines).
- [ ] **Step 5:** Commit: `feat(books): every financial record change posts its journal lines`.

---

### Task 4: Balance changes with no record, customer delete, safety net

**Files:**
- Modify `app.py`:
  - `_adjust_balance` (≈2955);
  - `create_reseller` (≈13132);
  - `create_upstream_provider` (≈13303);
  - `resolve_credit_review` (≈7246);
  - `_delete_customer_core` (≈5794);
  - a new `before_flush` capture and a `before_commit` safety net.
- Modify `customer_import.py` near lines 637–650.
- Test: `tests/test_ledger_cutover.py`

**Interfaces:**
- **Consumes:** Task 3 `_post_lines`, `_books_started`; Task 2 `balance_delta_to_lines`.
- **Produces:**
  - **`_ledger_note_manual(obj, party_type, delta_balance, memo)`:** for a started tenant, posts `balance_delta_to_lines` with kind `correction` and today's date. The posted party total feeds `session.info['ledger_party_posted']`, so the safety net sees it as explained.
  - **`_capture_balance_deltas`** (`before_flush`, registered before `_balance_changes_as_increments`): adds each `Customer`/`Reseller`/`Supplier`/`Employee`/`UpstreamProvider` balance history delta to `session.info['ledger_party_delta'][(tenant_id, party_type, id)]`.
  - **`_ledger_safety_net`** (`before_commit`):
    1. `session.flush()`.
    2. For each key, `unexplained = delta − posted` (posted converted with `party_balance_from_books`). If |unexplained| ≥ 0.005, post `balance_delta_to_lines` with kind `unexplained` and memo `"Unexplained balance change (<request.endpoint or 'background'>)"`.
    3. Clear both dicts.
    4. Also clear them in `after_rollback`.
  - **Customer delete:** `_delete_customer_core(customer)` sets `session.info.setdefault('ledger_deleting_customers', set()).add(customer.id)` before the cascade. `_post_ledger` skips `Payment` deletes whose `customer_id` is in that set. `_delete_customer_core` then posts one `correction`, with memo `"Customer deleted: <name>"`, moving the customer's books balance to `corrections`. The customer's own balance delta is marked explained.

- [ ] **Step 1: Write the failing tests:**
  - `test_hand_typed_balance_posts_correction` (via `update_supplier` with `balance` + `balance_reason`)
  - `test_new_reseller_opening_balance_posts_correction`
  - `test_new_upstream_opening_balance_posts_correction`
  - `test_credit_review_restore_posts_correction`
  - `test_import_opening_balance_posts` (customer import with `opening_balance` 30; expect a bill line, not a correction, since the import writes a `Payment`)
  - `test_customer_delete_keeps_money_and_writes_off_balance`: the customer paid $50 cash, has one open bill of $25 and $10 extra credit. After delete: Cash still +50, income unchanged, and one correction of the leftover balance.
  - `test_unmapped_balance_change_is_posted_unexplained`: in the app context, set `customer.balance -= 7` directly and commit. Expect one `unexplained` entry for 7, and books balance = stored balance.
- [ ] **Step 2:** Run the file. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the file, then the full suite. Expected: PASS and green.
- [ ] **Step 5:** Commit: `feat(books): no-record balance changes, customer delete, unexplained safety net`.

---

### Task 5: Starting the books (cut-over) and the nightly job

**Files:**
- Modify: `app.py`. Add `start_books`, `ledger_nightly`, the `_SCHEDULED_JOBS` entry, `SCHEDULE["ledger_nightly"] = "0 3 * * *"`, and `POST /api/books/start` (admin).
- Modify: `.github/workflows/scheduled-jobs.yml`: add a `0 3 * * *` line for `ledger_nightly`, mirroring the existing entries.
- Test: `tests/test_ledger_cutover.py`

**Interfaces:**
- **`start_books(tenant_id: int, now: datetime | None = None) -> LedgerState`:**
  - Idempotent: returns the existing state if already started.
  - In one transaction:
    1. Create the `CHART` accounts.
    2. Build the opening entry (kind `opening`, dated `start_day` = tenant-local date of `now`):
       - every party with |balance| ≥ 0.005, against `'opening'`;
       - Cash and Whish = `_cash_flow_for_day(start_day − 1 day, settings)['cash_end' / 'whish_end']`, with `None` → 0 and `last_check['cash_opening_unknown'] = True`.
    3. Insert `LedgerStartLine` rows for every existing record of every mapped model: `desired_lines` in its current state.
    4. Create `LedgerState`.
- **`ledger_nightly()`:** for every tenant, call `start_books` if not started, else `run_books_check` (Task 6). Each tenant runs in its own try/commit.
- **`POST /api/books/start`:** admin only. Calls `start_books(current_tenant_id())` and returns the state.

- [ ] **Step 1: Write the failing tests:**
  - `test_start_books_opening_matches_stored_balances`: books party balances equal stored balances for every party, Cash/Whish equal the Cash Flow closing figures, and the opening entry balances.
  - `test_start_books_is_idempotent`
  - `test_old_open_bill_paid_after_start_posts_only_the_receipt`
  - `test_old_bill_deleted_after_start_moves_income_to_opening`
  - `test_old_expense_edited_after_start_moves_expense_to_opening`
  - `test_old_bill_made_gratis_after_start_posts_discount_normally`
  - `test_cash_opening_unknown_flag_when_no_opening_set`
  - `test_ledger_nightly_starts_unstarted_tenants`
  - `test_schedule_matches_workflow`: the existing test must keep passing.
- [ ] **Step 2:** Run the file. Expected: FAIL.
- [ ] **Step 3:** Implement. Replace the Task 3 test helper with `start_books` in `tests/test_ledger_posting.py`.
- [ ] **Step 4:** Run the ledger test files, then the full suite. Expected: PASS and green.
- [ ] **Step 5:** Commit: `feat(books): start the books with opening balances and a starting point; nightly job`.

---

### Task 6: Nightly check and Books API

**Files:**
- Modify: `app.py`: `run_books_check`, `_books_cash_basis_profit`, and the routes `GET /api/books/accounts`, `GET /api/books/journal`, `GET /api/books/check`.
- Test: `tests/test_ledger_check.py`

**Interfaces:**
- **`run_books_check(tenant_id: int) -> dict`:** stores JSON in `LedgerState.last_check` and sets `last_check_at`. Keys:
  - `checked_at`;
  - `unbalanced_entries` (ids);
  - `party_mismatches`: list of `{party_type, party_id, name, stored, books}`;
  - `cash`: `{books, cash_flow}`;
  - `whish`: `{books, cash_flow}`;
  - `unexplained_count`, `problem_count`;
  - `profit_by_month`: list of `{month, books_cash_basis, reports}` for months from `start_day`.
  - **Comparisons:** `cash_flow` comes from `_cash_flow_for_day(today, settings)` `*_end`. `reports` uses the same monthly profit figure the Finance/Reports page shows today.
- **`_books_cash_basis_profit(tenant_id, month) -> float`:** for every Cash/Whish line in the month, follows its entry's other lines:
  - From `customers` or `resellers`: income.
  - To `upstream`, `suppliers`, `salaries` or any `expense*` / `cost_*` account: cost.
  - Transfers, `money_in`, `opening` and `over_short`: neither.
  - Returns income − cost.
- **`GET /api/books/accounts?as_of=YYYY-MM-DD`:** `{accounts: [{key, number, name, type, debit, credit, balance}], totals: {debit, credit}}` for lines with `entry_date <= as_of`.
- **`GET /api/books/journal?start_date&end_date&account&party_type&party_id&page&per_page`:** `{items: [{id, date, kind, memo, created_by, lines: [{account, party_type, party_id, party_name, debit, credit, source_type, source_id}]}], total}`, newest first.
- **`GET /api/books/check`:** `{started_at, start_day, last_check}`.
- All admin only. The other roles, finance included, get 403.

- [ ] **Step 1: Write the failing tests:**
  - `test_check_clean_after_normal_activity`: start books, run bills, payments, an expense, a transfer and a day close; the check shows no mismatches, cash/Whish equal, and profit months equal.
  - `test_check_flags_party_mismatch`: change `Supplier.balance` with a raw SQL UPDATE, which bypasses the listener; the check lists it.
  - `test_accounts_totals_balance`
  - `test_journal_filters_by_account_and_party`
  - `test_books_api_admin_only`: `finance`, `cashier` and `collector` get 403.
- [ ] **Step 2:** Run the file. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the file and the full suite. Expected: PASS and green.
- [ ] **Step 5:** Commit: `feat(books): nightly check and read-only Books API`.

---

### Task 7: Finance → Books tab

**Files:**
- Create: `frontend/src/components/BooksView.js`, `frontend/src/components/BooksView.test.js`
- Modify: `frontend/src/components/FinanceView.js` (add `{ key: 'books', label: 'Books', roles: ['admin'], render: () => <BooksView /> }` after `history`), `frontend/src/components/FinanceView.test.js` (admin tabs add `'Books'`), `frontend/src/context/AppContext.js` (`fetchBooksAccounts(asOf)`, `fetchBooksJournal(params)`, `fetchBooksCheck()`).

**Interfaces:** Consumes the Task 6 response shapes.

**Content (read-only, no action buttons):**
- **Header:** "Books started on <start_day>". If the books are not started: "Books start automatically tonight". If `cash_opening_unknown`: a note that the Cash/Whish opening is 0 because no opening balance was set on Cash Flow.
- **Accounts:** an as-of date picker, plus a table grouped by type (Assets, Liabilities, Equity, Income, Expenses) with Debit, Credit and Balance columns, and a totals row (Debit total = Credit total).
- **Journal:** date-range and account filters and a paged list. Each entry shows date, kind chip, memo and who, with its lines underneath (account, party name, debit, credit).
- **Check:** last check time and an "All agree" or "N to look at" summary, plus tables for party mismatches, cash/Whish, unexplained entries and profit by month (books vs reports).

- [ ] **Step 1: Write the failing tests** with the API mocked:
  - `renders accounts grouped with balanced totals`
  - `journal shows entry lines`
  - `check shows all agree when empty`
  - `check lists a mismatch`
  - `shows not-started message`
- [ ] **Step 2:** Run the frontend tests from a copy outside `.claude`. Expected: the new tests FAIL.
- [ ] **Step 3:** Implement, following the patterns in `AuditLogView.js`.
- [ ] **Step 4:** Run the frontend tests, then `npm run build` into the scratchpad. Expected: PASS, compiled.
- [ ] **Step 5:** Commit: `feat(books): Finance → Books tab (accounts, journal, check)`.

---

### Task 8: End-to-end verification

- [ ] **Step 1: Migration chain** on a copy of the real dev DB (in the scratchpad): upgrade, then `start_books` for each tenant, then `run_books_check`. Record the mismatch count. Expected: party mismatches 0 right after start, cash/Whish equal, and the downgrade/upgrade round trip works.
- [ ] **Step 2: Postgres run.** Run `tests/test_ledger_*.py` on a fresh Postgres 16 database (the local `pgserver` setup, `DATABASE_URL`). Expected: all PASS.
- [ ] **Step 3: Click-through** on the isolated local server (port 5077, scratch DB):
  1. Start the books.
  2. Receive a payment, revert it, and record an expense.
  3. Check the Books tab: accounts balanced, the journal shows the entries, the check is all-agree.
- [ ] **Step 4: Full suites.** Backend and frontend green.
- [ ] **Step 5: Update the roadmap doc's status line for 1C, and commit.**
