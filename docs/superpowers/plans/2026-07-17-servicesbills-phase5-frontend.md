# servicesBills — Phase 5: Frontend SaaS Experience (Implementation Plan)

> **For agentic workers:** REQUIRED SUB-SKILL for execution: superpowers:subagent-driven-development or superpowers:executing-plans.
>
> **REQUIRED DESIGN SUB-SKILLS (invoke for every visual/UX task):** the UI/UX Pro Max skills installed at `.claude/skills/brand/`, `.claude/skills/design/`, `.claude/skills/ui-ux-pro-max/`, `.claude/skills/ui-styling/`. Use `brand` to establish the servicesBills identity and `design`/`ui-ux-pro-max` for screens.
>
> **Prerequisite:** Phases 1–4 merged to `main` (multi-tenant backend + billing/signup APIs, 43 tests green). Do Phase 5 on a `phase-5-frontend` branch off `main`.

**Goal:** Build the customer-facing SaaS frontend on top of the Phase-4 APIs: public signup / email-verify / password-reset screens, plan selection + Stripe checkout redirect, manage-subscription, plan-limit upgrade prompts, a super-admin dashboard, per-tenant in-app branding, and a responsive web polish of the (formerly desktop/Electron) UI — all under a coherent servicesBills brand system.

**Architecture:** Introduce `react-router-dom` for **public/auth routes** (`/login`, `/register`, `/verify`, `/reset-password`, and the Stripe `/billing` return); the authenticated app stays a single route that keeps its existing `currentView` state-switch nav (low blast radius). All server calls go through the existing `AppContext` axios client (`apiService`) with the env-driven base URL from Phase 3. A single MUI theme (brand tokens) is applied app-wide; per-tenant logo/name from `BusinessSettings` themes the header.

**Tech Stack:** React 18 · MUI 5 · react-router-dom · axios · CRA (react-scripts) · Jest + React Testing Library.

## Global Constraints

- **All paths absolute** under `C:\Users\InfoCenter\source\repos\delta-net-saas\frontend\`; `delta-net` base untouched.
- **No new backend endpoints** — Phase 5 consumes the Phase-4 APIs (`/api/register`, `/api/verify-email`, `/api/forgot-password`, `/api/reset-password`, `/api/login`, `/api/billing/checkout`, `/api/billing/portal`, `/api/admin/*`, `/api/business-settings`). If a gap appears, note it; don't expand scope silently.
- **API base URL stays env-driven** (`REACT_APP_API_URL`, Phase 3) — never hardcode hosts.
- **Brand first:** establish the brand system (Task 5.0) before building screens so every screen uses the same tokens; do not hand-pick ad-hoc colors per screen.
- **Verification-driven:** each task is verified by running the app (superpowers `run` skill / webapp-testing Playwright skill) and, where practical, React Testing Library unit tests. Frontend testing is lighter than backend TDD — favor a few meaningful RTL tests (auth form logic, 402→upgrade prompt, role-gated dashboard) plus a manual browser pass.
- **Accessibility:** use the `design` skill's accessibility guidance (labels, focus, contrast) on all new forms/screens.
- **Frequent commits**, one per task. Keep the production bundle out of commits unless the repo's CI convention requires it (there is an "Auto-compile frontend production bundle" commit pattern — follow it only if asked).

---

## Task 5.0: Brand system + MUI theme + routing shell

**REQUIRED SKILL: `brand`** (establish identity), then wire tokens into MUI.

**Files:** new `frontend/src/theme.js`, new `frontend/src/brand/` (tokens/logo), `frontend/src/App.js` (ThemeProvider + router shell), `frontend/package.json` (+`react-router-dom`).

- [ ] **Step 1:** Invoke the `brand` skill to define the servicesBills identity (palette, typography, logo usage). Capture tokens in `frontend/src/brand/` and a servicesBills logo asset.
- [ ] **Step 2:** Create `theme.js` — a single MUI `createTheme` built from the brand tokens (primary/secondary, typography, shape, component defaults). Wrap the app in `<ThemeProvider theme={theme}>` + `<CssBaseline>`.
- [ ] **Step 3:** `npm install react-router-dom`. Introduce `<BrowserRouter>` with routes: public `/login`, `/register`, `/verify`, `/reset-password`, `/forgot-password`, `/billing/return`; and a catch-all `/*` that renders the existing authenticated app shell (guarded by `isAuthenticated`, else redirect to `/login`). Preserve the current `currentView` internal nav inside the authenticated shell.
- [ ] **Step 4: Verify:** `npm start`, app loads under the new theme, unauthenticated users land on `/login`, deep links resolve. Commit.

## Task 5.1: Auth screens (signup, login, verify, forgot/reset)

**REQUIRED SKILL: `design`** (form screens + accessibility).

**Files:** `frontend/src/components/RegisterView.js`, `LoginView.js`; new `VerifyEmailView.js`, `ForgotPasswordView.js`, `ResetPasswordView.js`.

- [ ] **Step 1:** Rework **RegisterView** into a SaaS signup: fields business_name, email, username, password → `POST /api/register`; on success show "check your email to verify" state. Link to `/login`.
- [ ] **Step 2:** **LoginView**: username + password → `AppContext.login`; links to `/register` and `/forgot-password`. Surface the 402 "subscription inactive"/"verify email" states from the API as friendly banners.
- [ ] **Step 3:** **VerifyEmailView** (`/verify?token=…`): on mount, `POST /api/verify-email {token}` → success/failure state with a link to log in.
- [ ] **Step 4:** **ForgotPasswordView** (`/forgot-password`): email → `POST /api/forgot-password`; always show the same "if that email exists…" confirmation (mirror the no-enumeration backend).
- [ ] **Step 5:** **ResetPasswordView** (`/reset-password?token=…`): new-password form → `POST /api/reset-password {token,new_password}` → success → redirect to `/login`.
- [ ] **Step 6:** RTL tests: RegisterView posts the right payload and shows the verify-email state; ResetPasswordView reads the token from the query string. Verify in-browser. Commit.

## Task 5.2: Billing — plan selection, checkout, manage subscription

**REQUIRED SKILL: `design`** (pricing/plan cards).

**Files:** new `frontend/src/components/BillingView.js`; `App.js` (nav item + `/billing/return` route).

- [ ] **Step 1:** **BillingView** shows current plan/status (from `AppContext.user`/a `GET /api/business-settings` or a small tenant-info call) and plan cards from a client mirror of `plans` (name, price, features). "Upgrade to Pro" → `POST /api/billing/checkout {plan:"pro"}` → `window.location = res.url` (Stripe-hosted checkout).
- [ ] **Step 2:** "Manage subscription" (visible when a subscription exists) → `POST /api/billing/portal` → redirect to the returned portal URL.
- [ ] **Step 3:** `/billing/return` route reads `?status=success|cancel` and shows a confirmation/"processing—your plan updates shortly" message (the webhook is the source of truth, so don't assume immediate plan change).
- [ ] **Step 4:** Add a "Billing" nav item (admin only). RTL test: clicking Upgrade calls checkout and redirects to the returned URL (mock axios + `window.location`). Verify in-browser (Stripe test mode). Commit.

## Task 5.3: Plan-limit upgrade prompts (402 handling)

**Files:** `frontend/src/context/AppContext.js` (axios response interceptor), a shared `UpgradeDialog`.

- [ ] **Step 1:** Add an axios response interceptor: on `402`, surface a global "Upgrade required" dialog/snackbar linking to `/billing` (message from the API body). Ensure it does NOT fire for billing routes themselves.
- [ ] **Step 2:** In the customer-add flow, show the upgrade prompt when `POST /api/customers` returns 402 (free customer cap). RTL test: a mocked 402 triggers the dialog. Verify. Commit.

## Task 5.4: Super-admin dashboard

**REQUIRED SKILL: `design`** (data-dense admin table).

**Files:** new `frontend/src/components/SuperAdminView.js`; `App.js` (route/nav gated by `role === 'superadmin'`).

- [ ] **Step 1:** For a super-admin login (role `superadmin`, no tenant), render a dedicated dashboard instead of the tenant app: `GET /api/admin/tenants` → table (name, plan, status, customers, users) with suspend/reactivate/delete actions (`POST /api/admin/tenants/<id>/suspend|reactivate`, `DELETE /api/admin/tenants/<id>`), delete behind a typed confirm.
- [ ] **Step 2:** Route super-admins to `/admin` post-login; keep them out of tenant views. RTL test: super-admin sees the tenant list; a tenant admin does not. Verify. Commit.

## Task 5.5: Per-tenant in-app branding

**Files:** `frontend/src/App.js` (app bar), `SettingsView.js` (already uploads logo/name).

- [ ] **Step 1:** Use `businessSettings` (logo_url via the Phase-3 `storage.url`, business_name) to brand the authenticated app bar/header per tenant, falling back to the servicesBills brand when unset.
- [ ] **Step 2:** Verify a tenant's uploaded logo/name renders in-app; the marketing/auth screens keep the servicesBills brand. Commit.

## Task 5.6: Responsive web polish

**REQUIRED SKILL: `ui-ux-pro-max`** (responsive/layout).

**Files:** across `frontend/src/components/*` (audit), `App.js` (nav drawer already exists).

- [ ] **Step 1:** Audit the desktop-first (Electron-era) layouts for browser + mobile: the DataGrid views (customers/payments/receipts), forms, and the nav drawer. Fix obvious overflow/tap-target/viewport issues; ensure the app is usable at mobile widths.
- [ ] **Step 2:** Verify at desktop/tablet/mobile breakpoints (resize in the browser tool). Commit.

## Task 5.7: Marketing / landing page (optional, brand-led)

**REQUIRED SKILL: `design` + `brand`.**

**Files:** new `frontend/src/components/LandingView.js`; `/` route for unauthenticated visitors.

- [ ] **Step 1:** A servicesBills landing page (hero, features, pricing, signup CTA) at `/` for logged-out visitors, linking to `/register`. Keep it lightweight and on-brand.
- [ ] **Step 2:** Verify; commit. (Skip if a separate marketing site is preferred — note the decision.)

---

## Notes / decisions to confirm at execution

- **Router scope:** public/auth routes use react-router; the authenticated app keeps its `currentView` state-switch to avoid a large nav refactor. If a full router migration of internal nav is wanted, that's a separate follow-up.
- **Plan mirror:** the frontend needs plan names/prices/features for the pricing cards; keep a small client-side constant in sync with backend `plans.py` (or add a `GET /api/plans` endpoint — a small, justified backend addition if preferred).
- **CI bundle:** the repo has an auto-compiled production bundle commit pattern; follow it only if the deploy path serves the prebuilt `build/`. The Phase-3 Dockerfile builds the SPA itself, so committing `build/` may be unnecessary.

## Self-Review

**Coverage:** Maps every Phase-5 item from the master plan — landing (5.7), auth/verify/reset screens (5.1), billing UI + checkout + portal (5.2) with limit prompts (5.3), per-tenant branding (5.5), super-admin console UI (5.4), brand system (5.0), responsive polish (5.6). Each visual task names the required UI/UX Pro Max sub-skill.

**Placeholder scan:** Screens are specified by their exact API calls, fields, routes, and files; visual specifics are intentionally delegated to the `brand`/`design` skills (invoked per task) rather than hardcoded here — that is the correct division for design work, not a placeholder. Backend endpoints referenced are the real Phase-4 routes.

**Consistency:** Uses the existing `AppContext` (`login`, `user`, `isAuthenticated`, `apiService`) and `businessSettings`/`SettingsView` already in the codebase; new routes align with the Phase-4 email-link URLs (`APP_BASE_URL/verify?token=…`, `/reset-password?token=…`, `/billing?status=…`).

## Execution Handoff

Order: 5.0 (brand+theme+router) → 5.1 (auth) → 5.2 (billing) → 5.3 (limit prompts) → 5.4 (super-admin) → 5.5 (branding) → 5.6 (responsive) → 5.7 (landing, optional). Invoke the UI/UX Pro Max skills per task. Verify each in the browser; commit per task. When done, merge `phase-5-frontend` → `main` — completing the delta-net → servicesBills SaaS transformation.
