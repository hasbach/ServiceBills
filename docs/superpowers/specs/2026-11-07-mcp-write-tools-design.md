# ServiceBills MCP connector — write tools

Date: 2026-11-07
Status: implemented on feat/mcp-write-tools (plan: docs/superpowers/plans/2026-11-07-mcp-write-tools.md)
Builds on: `2026-10-21-mcp-connector-design.md` (read-only v1, live since 2026-10-22)

## Goal

Let a connected staff member *do* daily operations from their AI app, not just
read: add a customer, renew, take a payment, record an expense or a salary,
open a ticket — with exactly the rights they already have in the web app.

## Decisions

| Question | Decision |
|---|---|
| Scope | Daily operations: customers/subscriptions, customer money, expenses, cash, suppliers, payroll, resellers, tickets |
| Approach | Curated, typed tools (24 write + 3 read helpers). No generic "call any endpoint" tool |
| Business logic | Every write calls the app's existing endpoint in-process as the signed-in user; no new direct DB writes |
| Switch | Per-business admin toggle, **off by default**; read-only until an admin turns it on |
| Removals | Undo-style only (revert, refund, cancel subscription, delete an expense, cash entry, account transfer or supplier payment). No deleting customers/suppliers/employees/tickets, no bulk actions, no day close |
| Balance | Never editable through the AI (no `balance` on `update_customer`, no fix-balance tools) |

Out of scope: users, settings, subscription plans, network device config,
WhatsApp config, day close / reopen, bulk operations, hard deletes of master
records, generic endpoint access.

## Architecture

### Calling endpoints

`call_api(ctx, path, params=None)` in `mcp_tools.py` grows `method` and `json`
arguments (default `GET`, no body — existing callers unchanged). A write tool:

1. validates its arguments with the existing `*_arg` helpers plus a new
   `amount_arg` (positive number, at most 2 decimals);
2. resolves and tenant-checks any ids it was given (`scoped(ctx, Model)`), so a
   wrong id gives "Customer 123 not found" rather than an endpoint 404. As
   built, only `find_payments` and `create_ticket` pre-check; the other tools
   relay the endpoint's own not-found message (the endpoint is tenant-scoped
   either way);
3. calls the endpoint with `call_api(ctx, path, method=..., json=...)`;
4. returns a short confirmation built from the endpoint's response and/or a
   fresh read of the changed record (id, name, new expiry, amount, balance).

The endpoint's `@jwt_required`, role/module decorators, day-close lock,
balance log, upstream sync and WhatsApp sends all run as they do for a web
click. The endpoint commits its own work.

### The switch

- New column `BusinessSettings.ai_connector_writes_enabled` (Boolean, not null,
  default false). Migration with a random revision id, single head, and the
  lock_timeout + savepoint retry pattern from PR #35.
- Exposed on the existing AI connector settings API and toggled from
  `frontend/src/components/AIConnectorCard.js` ("Allow the AI to make
  changes"), admin only.
- Read per request (no caching) so the change applies on the next call; no
  reconnect.
- When off: `tools/list` omits every write tool; a direct `tools/call` of one
  returns the tool error "Changes through the AI are turned off for this
  business. An admin can turn them on in Settings → AI connector."
- `INSTRUCTIONS` (initialize) is chosen per request: the current read-only text
  when off; when on, the same text without "You cannot change anything", plus
  "Write tools change real data — confirm amounts and the customer with the
  user before calling them."

### Tool definition

`Tool` gets `writes: bool = False`, `destructive: bool = False`,
`dedupe: bool = False`. `_describe` emits
`readOnlyHint: not writes`, `destructiveHint: destructive`,
`idempotentHint: False` for writes. `tools/list` filters by role, module and,
for `writes`, the switch.

## Tools

Roles listed are who sees the tool; the endpoint's own check remains the
authority. ⚠ = `destructive`, requires a `reason` argument (passed to the
endpoint where it takes one, otherwise kept in the audit summary).
$ = `dedupe` (double-submit guard, below).

### Customers and subscriptions — admin, finance, cashier

| Tool | Endpoint | Inputs |
|---|---|---|
| `create_customer` $ | POST `/api/customers` | name*, phone, address, sector, subscription_plan_id*, subscription_start_date, reseller_id, upstream_provider_id, upstream_username, pppoe_username, cpe_mac_address, discount, notes |
| `update_customer` | PUT `/api/customers/<id>` | customer_id* + any of the above + whatsapp_notifications_enabled. No `balance`, no `is_subscription_active` |
| `renew_subscription` $ | POST `/api/customers/<id>/renew_subscription` | customer_id* |
| `activate_subscription` | PUT `/api/customers/<id>/activate_subscription` | customer_id* |
| `cancel_subscription` ⚠ | PUT `/api/customers/<id>/cancel_subscription` | customer_id*, reason* |
| `network_suspend` / `network_unsuspend` | POST `/api/customers/<id>/network-suspend` / `-unsuspend` | customer_id* — admin, finance; module `network` |

### Customer money

| Tool | Endpoint | Inputs | Roles |
|---|---|---|---|
| `receive_payment` $ | POST `/api/customers/<id>/receive-payment` | customer_id*, amount*, method (cash \| whish_transfer), reference, action (pay \| collect; default pay for admin/finance, collect otherwise) | admin, finance, cashier, collector |
| `confirm_collected` | POST `/api/customers/<id>/confirm-collected` | customer_id* | admin, finance |
| `revert_payment` ⚠ | PUT `/api/payments/<id>/revert` | payment_id*, reason* | admin, finance |
| `refund_payment` ⚠ $ | POST `/api/payments/<id>/refund` | payment_id*, amount*, reason*, paid_via | admin, finance |
| `mark_payment_gratis` | PUT `/api/payments/<id>/mark_gratis` | payment_id*, note | admin, finance |

### Expenses, cash, suppliers — admin, finance

| Tool | Endpoint | Inputs |
|---|---|---|
| `add_expense` $ | POST `/api/expenses` | amount*, category*, date, description, supplier_id, employee_id, is_credit, paid_via |
| `update_expense` | PUT `/api/expenses/<id>` | expense_id* + any of the above |
| `delete_expense` ⚠ | DELETE `/api/expenses/<id>` | expense_id*, reason* |
| `add_cash_entry` $ | POST `/api/cash-entries` | account*, amount*, date, reason* |
| `delete_cash_entry` ⚠ | DELETE `/api/cash-entries/<id>` | entry_id*, reason* — admin only |
| `transfer_between_accounts` $ | POST `/api/account-transfers` | from_account*, to_account*, amount*, note |
| `delete_transfer` ⚠ | DELETE `/api/account-transfers/<id>` | transfer_id*, reason* — admin only |
| `pay_supplier` $ | POST `/api/suppliers/<id>/payments` | supplier_id*, amount*, payment_date, payment_method, reference_note, paid_via |
| `delete_supplier_payment` ⚠ | DELETE `/api/suppliers/<id>/payments/<pid>` | supplier_id*, payment_id*, reason* — admin only |

### Payroll — admin only

| Tool | Endpoint | Inputs |
|---|---|---|
| `add_employee_charge` $ | POST `/api/employees/<id>/charges` | employee_id*, type*, amount*, period, date, reason |
| `pay_employee` $ | POST `/api/employees/<id>/payments` | employee_id*, amount*, is_advance, method, payment_date, note, paid_via |

### Resellers — admin, finance

| Tool | Endpoint | Inputs |
|---|---|---|
| `reseller_add_credit` $ | POST `/api/resellers/<id>/add_credit` | reseller_id*, amount*, description |
| `reseller_collect_payment` $ | POST `/api/resellers/<id>/collect_payment` | reseller_id*, amount*, description, paid_via |

### Tickets — all staff

| Tool | Endpoint | Inputs |
|---|---|---|
| `create_ticket` $ | POST `/api/support-tickets` | customer_id*, title*, description, priority |
| `update_ticket` | PUT `/api/support-tickets/<id>` | ticket_id*, status, priority, title, description |

### Read helpers (read-only, always listed)

| Tool | Returns | Roles |
|---|---|---|
| `list_plans` | Subscription plans: id, name, price, cycle | all staff |
| `list_expense_categories` | Category names | admin, finance |
| `find_payments` | One customer's recent payments (up to 20) with ids, amounts, status, reverted flag | admin, finance, cashier |

Cash entries are cash-in only (the endpoint takes a positive amount). `paid_via` is `cash` (default) or `whish` — which account the money left or reached; without it everything books as cash. Ticket priorities: low, medium, high, critical (as the web app offers).

`*` = required. Exact enum values (ticket status/priority, charge type, cash
account names, payment methods) are taken from the endpoint code during
planning and put into each tool's JSON schema as `enum`.

## Errors

- 400 from an endpoint: relay its `message`/`msg`/`error` string only if it is
  a plain sentence under 200 characters with no newline, `Traceback`,
  `Exception`, `psycopg2`/`sqlalchemy` text; otherwise "ServiceBills rejected
  this change (400)." The read path keeps today's stricter behaviour.
- 403/404: as today (relay the permission / not-found wording).
- Any write answered with 4xx/5xx, or a `ToolError` from a write tool, rolls
  back the shared session before the audit row is written, so ORM changes an
  endpoint made before it refused are never committed with the audit.
- 5xx or exception: roll back, generic "ServiceBills hit an error (500). Check
  in ServiceBills whether it was saved before trying again." Some endpoints
  commit and then fail later (profit recalculation, staged renew), so the AI
  must not be told nothing changed; a failed call is not counted by the
  double-submit guard, so this wording is what stops a blind retry.
- Argument errors are raised before any call, so a bad input never reaches the
  endpoint.

## Double-submit guard

AI apps can retry a call after a timeout. For `dedupe` tools, before calling
the endpoint: if `McpToolCall` has a row with the same user, tool and
`args_summary` (computed without `confirm_duplicate`), `ok = true`, within the
last 2 minutes, refuse with "This exact change was already made at HH:MM. Call
again with confirm_duplicate: true if you really want it twice." Every
`dedupe` tool accepts an optional boolean `confirm_duplicate`.

## Audit

`McpToolCall` gains `wrote` (Boolean, not null, default false; same migration),
set true for write tools. Arguments, user, grant, result and duration are
already recorded. No UI for it in this release.

## Testing

- Per-group unit tests (`tests/test_mcp_tools_write_*.py`, following
  `tests/mcp_helpers.py`): success changes the DB as the web call would;
  role refused; switch off refuses and hides; bad arguments rejected before
  any DB change; tenant isolation (another tenant's id → not found).
- Double-submit guard: second identical call refused, `confirm_duplicate`
  passes, different args pass, failed first call does not block.
- `tools/list`: write tools only with switch on + matching role; annotations.
- 400 message relay: safe sentence passed, exception-like text masked.
- `test_mcp_e2e.py`: one write through `/mcp` with a real OAuth token.
- Settings API + `AIConnectorCard` toggle (admin only), frontend test.
- After deploy (owner): turn the toggle on for Delta Net, from Claude do a
  renew, a payment, an expense and a ticket, check them in the web app.
