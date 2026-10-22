# ServiceBills MCP connector — design

Date: 2026-10-21
Status: approved design, not yet planned or built

## Goal

Let an ISP's own staff connect their AI app (Claude.ai, ChatGPT, Gemini CLI,
Cursor, Claude Desktop/Code, any MCP client) to their ServiceBills tenant as a
custom connector, and ask it questions about their business ("who expires this
week?", "how much cash came in yesterday?", "what's Ali's balance?").

## Decisions

| Question | Decision |
|---|---|
| Who connects | Each ISP's staff, signed in as their own ServiceBills user, confined to their tenant and role |
| Scope of v1 | Read-only. No money moves, nothing is sent, no live device jobs |
| Where | Cloud SaaS (Render) only. On-prem installs are out of scope (LAN, unreachable by cloud AI apps) |
| Pricing | New paid module `ai_connector` |
| Auth | OAuth 2.1 authorization server inside the Flask app (only option that works as a Claude.ai / ChatGPT connector) |
| Server | In-process `POST /mcp` endpoint in the existing Flask app, stateless Streamable HTTP, hand-written JSON-RPC (no MCP SDK, no ASGI) |

Out of scope for v1: write tools, payroll/salaries, WhatsApp conversation
history, personal API tokens, on-prem, super-admin cross-tenant access.

## Tools

All tools are read-only (`readOnlyHint: true`), tenant-scoped through
`tenant_query`, and gated by the user's *current* role (re-read from the DB on
every call). Every result carries `as_of` in the business timezone.

| Tool | Returns | Roles | Module |
|---|---|---|---|
| `search_customers` | Matches by name, phone (via `normalize_lebanese_phone`), username or MAC | all staff | — |
| `get_customer` | Plan, status, expiry, balance/credit, upstream, last payments, open tickets | all staff | — |
| `list_customers` | Filter by status, expiring within N days, sector, plan, upstream, owing | all staff | — |
| `list_payments` | Payments in a date range, by collector/channel, reverted excluded via `_payment_not_reverted()` | admin, finance, cashier | — |
| `get_cash_flow` | Money in/out per day or range by channel (cash/Whish), day-close status | admin, finance | — |
| `get_financial_summary` | Revenue, expenses, estimated profit for a month | admin, finance | — |
| `list_tickets` | Support tickets by status or customer | all staff | — |
| `get_network_status` | Device health, active alerts, cached last-known ONU state for a customer (no live polling) | `NETWORK_VIEW_ROLES` | `network` |
| `list_resellers` | Reseller balances and status | admin, finance | — |
| `list_upstreams` | Upstream provider balances and status | admin, finance | — |

"All staff" means every tenant role (admin, finance, employee, collector,
cashier). List results are capped at 50 rows with a "narrow your filter"
note. The exact field set of each tool is fixed in the implementation plan;
nothing in it may exceed what the same role sees in the web app.

## OAuth flow

Endpoints (all on the app's public origin):

- `GET /.well-known/oauth-protected-resource` (RFC 9728) names the
  authorization server.
- `GET /.well-known/oauth-authorization-server` (RFC 8414) gives metadata:
  `authorization_code` + `refresh_token` grants, `S256` only, `none` client
  auth for public clients, registration endpoint.
- `POST /oauth/register` (RFC 7591) handles dynamic client registration. It stores the client
  name and redirect URIs. Redirect URIs must be `https://` or a loopback
  (`http://localhost` / `127.0.0.1`).
- `GET /oauth/authorize` validates client_id, redirect_uri (exact match),
  `code_challenge` (S256 required), state, then hands off to a frontend
  consent route.
- Frontend consent page: if not logged in, the normal ServiceBills login runs first
  (existing tenant/user flow); then shows *"\<client name\> wants read-only
  access to \<tenant\> as \<user\> (\<role\>). Allow / Deny."* Allow posts to a
  JWT-protected backend endpoint, which issues a single-use auth code (60s)
  and returns the redirect URL.
- `POST /oauth/token` exchanges the code with the PKCE verifier for an access token (1h) and a refresh token (30d).
  Refresh rotates the refresh token; reuse of an already-rotated refresh token
  revokes the whole grant.
- `POST /oauth/revoke` (RFC 7009) revokes a token.

Access checks on every `/mcp` request: token hash matches an unrevoked,
unexpired grant; user still exists and is active; tenant active; `ai_connector`
enabled for the tenant (`modules.enabled_for`); then the per-tool role/module
check. Any revocation, demotion, suspension or module switch-off takes effect on
the next request.

## Data model (one Alembic migration, random revision id, single head)

- `McpClient`: client_id, client_name, redirect_uris (JSON), created_at.
  Not tenant-scoped (a registered app like "Claude" is shared).
- `McpGrant`: tenant_id, user_id, client_id, scope, access_token_hash,
  access_expires_at, refresh_token_hash, refresh_expires_at, created_at,
  last_used_at, revoked_at. Auth codes are held on the grant row while pending
  (code_hash, code_challenge, redirect_uri, code_expires_at).
- `McpToolCall`: tenant_id, user_id, grant_id, tool, args_summary
  (truncated), ok, error, duration_ms, created_at.

Tokens are 32-byte random URL-safe strings; only SHA-256 hashes are stored.

## MCP protocol

`POST /mcp` uses stateless Streamable HTTP and returns `application/json` responses only.
Handles `initialize` (advertise the current spec version clients send, accept
the previous one), `notifications/initialized` (202), `tools/list`,
`tools/call`, `ping`. `GET /mcp` gives 405. Batch requests are not supported.
Rate limit: 60 requests/minute per grant (Flask-Limiter).

## Errors

- Missing/invalid/expired token: 401 with
  `WWW-Authenticate: Bearer resource_metadata="<origin>/.well-known/oauth-protected-resource"`.
- Module off / tenant suspended: 403 with a plain-language message.
- Role or module missing for one tool: tool result `isError: true` with a
  sentence the AI can relay ("Your role (cashier) can't view cash flow").
- Bad arguments or unknown tool name: JSON-RPC `-32602`. Unknown method:
  `-32601`.
- Not found: empty result plus a hint, never an exception.
- Every `tools/call` (success or failure) writes one `McpToolCall` row.

## UI

- Settings → **Connected AI apps**: each user lists and revokes their own
  grants (client name, created, last used). Admins see and revoke every grant
  in the tenant. It shows the `/mcp` URL with copy-paste setup notes for Claude.ai
  and ChatGPT.
- Super-admin module toggles gain `ai_connector` (existing override UI).
- `modules.py`: add `ai_connector` to `PAID`; `plans.py`: add it to the
  `pro` bundle.

## Code layout

Follows the `whatsapp_inbox.py` / `whatsapp_inbox_routes.py` split:

- `mcp_server.py`: JSON-RPC dispatch, tool registry (name, input schema,
  roles, module, handler), auth check for `/mcp`.
- `mcp_tools.py`: read-only tool handlers.
- `mcp_oauth.py`: discovery, register, authorize, consent-approve, token,
  revoke routes and token helpers.
- Migration for the three tables.
- Frontend: consent page, Connected AI apps settings page.

## Testing

- OAuth: full code+PKCE flow; wrong verifier rejected; code single-use and
  60s expiry; redirect_uri mismatch rejected; refresh rotation; rotated-token
  reuse revokes grant; revoked grant rejected.
- Isolation: tenant A's token never returns tenant B's customers/payments.
- Gating: each tool vs. each role; `network` and `ai_connector` module off.
- Protocol: initialize / tools/list / tools/call / ping / unknown method.
- Live: MCP Inspector against the local server; after deploy, add the real URL
  as a custom connector in Claude.ai and ChatGPT and ask real DeltaNet
  questions.

## Later (not v1)

Write tools (renew, record payment, send WhatsApp) with per-call confirmation;
personal API tokens for dev tools on the same grant table; on-prem via tunnel;
WhatsApp history for admins.
