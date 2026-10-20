# Feature Specification: Automatic WhatsApp Connection (Coexistence / New API Number / Manual)

**Feature Branch**: `feat/whatsapp-auto-connect`

**Created**: 2026-10-20

**Status**: Draft

**Input**: User description: "Automatic WhatsApp Business API connection for tenants. Today a tenant connects WhatsApp only by manually pasting Meta Cloud API credentials (phone number ID, WABA ID, access token, etc.) into Settings. Add two automatic connection methods alongside the existing manual config: (1) WhatsApp Business App "coexistence" onboarding — tenant keeps using their existing WhatsApp Business app number on their phone while the platform also gets Cloud API access to the same number; (2) new WhatsApp Business API number via Meta Embedded Signup — tenant logs in with Facebook, creates/selects a business + WABA + phone number, and the platform receives the credentials automatically. Manual config remains available as the third option. The tenant picks a method in Settings > Messaging."

## Context

Today, a tenant (an ISP reseller using the platform) who wants automatic WhatsApp sending ("API mode") must create their own Meta developer app, then copy six or more values into Settings > Messaging by hand: phone number ID, business account ID, app ID, app secret, access token, and a webhook verify token. They must also register the platform's webhook address in Meta themselves. Most tenants can't do this alone, so onboarding depends on platform staff, and a single typo breaks sending without saying why.

This feature lets a tenant connect by signing in to Meta from inside the platform and approving access. The platform then receives and stores everything it needs. There are two automatic paths, depending on whether the tenant wants to keep using WhatsApp on their phone.

## Clarifications

### Session 2026-10-20

- Q: Coexistence chat history: import past phone-app history, or only new messages? → A: Only messages from the moment of connection onward. No history import.
- Q: Message templates after connecting? → A: A one-click "Submit standard templates" action. The tenant can edit each template's text before submitting, while the required variables must stay in place (enforced).
- Q: Does manual setup mean the tenant enters all the WhatsApp API fields themselves? → A: Yes, it works exactly as today, with their own Meta app and every credential field entered by hand (see FR-017).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Connect an existing WhatsApp Business app number (coexistence) (Priority: P1)

A tenant already runs customer service from the WhatsApp Business app on a phone, and customers know that number. In Settings > Messaging they choose "Connect my WhatsApp Business app number". A Meta sign-in window opens. They sign in with Facebook, pick or create their business, and enter their existing number. They confirm on the phone by scanning the QR code or accepting the prompt in the WhatsApp Business app. When the window closes, the platform shows "Connected", with the number and business name. From then on, payment, renewal and reminder messages go out automatically from that same number, and the tenant can still chat from the phone.

**Why this priority**: This is the most common tenant situation (a Lebanese ISP reseller with one business WhatsApp number on a phone), and it removes the biggest objection to API mode: "I'll lose my WhatsApp app". It delivers full value on its own.

**Independent Test**: Use a test tenant with a WhatsApp Business app number. Run the coexistence flow, then trigger a payment confirmation. The customer receives it from the same number, and the phone app still sends and receives messages.

**Acceptance Scenarios**:

1. **Given** a tenant on a plan that includes API messaging with no WhatsApp connection, **When** they finish the coexistence flow in the Meta window, **Then** Settings > Messaging shows the status "Connected", the method "WhatsApp Business app (coexistence)", the display phone number and the business name, and messaging mode switches to automatic sending.
2. **Given** a tenant connected through coexistence, **When** a payment is recorded for a customer, **Then** the customer receives the payment-confirmation message from the tenant's existing number without anyone typing credentials.
3. **Given** a tenant connected through coexistence, **When** a customer sends a message to the number, **Then** it appears both in the tenant's phone app and in the platform's WhatsApp Inbox.
4. **Given** a tenant connected through coexistence, **When** the tenant replies from the phone app, **Then** the reply appears in the platform Inbox conversation and is marked as sent from the phone.
5. **Given** a tenant who closes the Meta window before finishing, **When** they return to Settings, **Then** nothing about their previous WhatsApp configuration has changed, and they see a message that the connection was not completed.

---

### User Story 2 - Connect a new number dedicated to the API (Priority: P2)

A tenant wants a number used only by the platform: a new SIM, or a number not yet on WhatsApp. In Settings > Messaging they choose "Connect a new number (WhatsApp Business API)". In the Meta window they sign in, create or select their business and WhatsApp business account, add the phone number, and verify it with a code by SMS or voice call. When the window closes, the platform shows "Connected", and automatic sending works.

**Why this priority**: This path is needed for tenants without a business app number, or who want the platform separated from staff phones. It matters less than P1 because fewer tenants start in this position.

**Independent Test**: Use a test tenant and a fresh number. Finish the flow, send a test message from Settings, and confirm it arrives.

**Acceptance Scenarios**:

1. **Given** a tenant with no WhatsApp connection, **When** they finish the new-number flow, **Then** status shows "Connected", the method shows "WhatsApp Business API (new number)", and a test message can be sent from Settings.
2. **Given** a tenant who selects a number that is already active in the WhatsApp app (not the Business app), **When** Meta rejects it, **Then** the platform shows Meta's reason in plain language and suggests the coexistence option or a different number.

---

### User Story 3 - Keep manual configuration as a third option (Priority: P2)

A tenant, or platform staff working for them, who already has their own Meta app and credentials chooses "Manual configuration" and sees the existing credential form, which works exactly as it does today.

**Why this priority**: Tenants already connected today must not break, and some advanced tenants want full control. It has P2 priority because it already exists; the work is to keep it working and present it as one of three choices.

**Independent Test**: For a tenant already configured manually, deploy the feature and confirm that sending, the Inbox, template management and webhooks all keep working with no action from the tenant.

**Acceptance Scenarios**:

1. **Given** a tenant already configured manually before this feature, **When** the feature is released, **Then** their settings show method "Manual configuration", and all messaging keeps working with no changes.
2. **Given** a tenant choosing "Manual configuration", **When** they save credentials, **Then** the behavior is identical to today's form.

---

### User Story 4 - See connection health, reconnect, and disconnect (Priority: P3)

A connected tenant opens Settings > Messaging and sees: the connection method, the number, the business name, the quality rating, the messaging limit tier, and whether payment is set up in Meta. If the connection breaks (access revoked in Meta, number removed, or the phone app unlinked in coexistence), the status changes to "Needs attention", with a "Reconnect" button. A tenant can also "Disconnect", which stops all automatic sending and returns them to manual-link mode.

**Why this priority**: Without this, a broken connection fails silently, which is the same pain the feature is meant to remove. It ranks below P1 and P2 because those must work before there is anything to monitor.

**Independent Test**: Revoke the platform's access to a connected test tenant in Meta Business settings. Within the detection window, Settings shows "Needs attention", and clicking "Reconnect" restores sending.

**Acceptance Scenarios**:

1. **Given** a connected tenant, **When** they open Settings > Messaging, **Then** they see the method, the number, the business name, the quality rating, the messaging limit, and the payment-method status.
2. **Given** a connected tenant whose access was revoked in Meta, **When** the platform next fails to send or receives a revocation notice from Meta, **Then** status becomes "Needs attention", tenant admins see an in-app alert, and automatic sends fail with a visible reason instead of silently.
3. **Given** a connected tenant, **When** they click "Disconnect" and confirm, **Then** automatic sending stops, the stored credentials are removed, messaging falls back to manual links, and the history of past messages stays.

---

### Edge Cases

- **Number already connected to another tenant**: the platform refuses to connect a number or business account already linked to a different tenant. It tells the user so, and does not overwrite the other tenant's connection.
- **Switching methods while connected**: a tenant switching from manual to automatic (or between the automatic methods) must confirm first. The new connection replaces the old one only after the new flow succeeds. If the new flow fails, the old connection stays.
- **Coexistence number not eligible** (for example, a WhatsApp app number that is not a Business app number, a Business app version that is too old, an unsupported country, or a number not active long enough): Meta's reason is shown in plain language, with the new-number path offered as an alternative.
- **No payment method in Meta**: the connection succeeds, but the platform warns that business-started messages (payment confirmations, reminders) will not deliver until a payment method is added in Meta. It links to where to do that.
- **No approved message templates on a newly connected business account**: the platform tells the tenant that automatic messages need approved templates, and offers the "Submit standard templates" action (FR-015).
- **Tenant edits a template and deletes a variable**: submission of that template is blocked, and the missing variable is named. The other selected templates can still be submitted.
- **A template with the same name already exists on the business account** (for example, a number that was used with another tool before): the platform shows the existing one and its status instead of submitting a duplicate. The tenant can choose to keep it or edit it.
- **Two tenant admins start the flow at the same time**: only the first one to finish is saved. The second sees that a connection already exists.
- **Plan without API messaging**: the automatic options are visible but locked, with an upgrade prompt, the same as API mode today.
- **Coexistence phone app unlinked by the tenant on the phone**: treated as "Needs attention", as in User Story 4.
- **Meta sign-in window blocked by a pop-up blocker**: the tenant is told to allow pop-ups and retry.

## Requirements *(mandatory)*

### Functional Requirements

**Method selection**

- **FR-001**: Settings > Messaging MUST offer three connection methods: "WhatsApp Business app (coexistence)", "New number (WhatsApp Business API)" and "Manual configuration". Each comes with a one-line explanation of when to use it.
- **FR-002**: The two automatic methods MUST follow the same plan gating as API messaging today. Tenants whose plan does not include API messaging see them locked, with an upgrade prompt.
- **FR-003**: Only users allowed to change tenant settings MUST be able to start, replace or remove a WhatsApp connection.

**Automatic connection (both automatic methods)**

- **FR-004**: The tenant MUST be able to finish the whole connection by signing in to Meta and approving access inside a window opened from the platform, without copying any identifier, secret or token by hand.
- **FR-005**: When the flow finishes successfully, the platform MUST obtain and store, for that tenant, everything it needs to send and receive messages on the connected number. Secrets MUST be stored encrypted, as they are today.
- **FR-006**: When the flow finishes successfully, the platform MUST register itself to receive that business account's incoming messages and status updates, with no manual webhook setup by the tenant.
- **FR-007**: When the flow finishes successfully, the platform MUST switch the tenant to automatic sending, and show the connected number and business name.
- **FR-008**: If the flow is cancelled, fails, or returns incomplete data, the platform MUST leave the tenant's existing WhatsApp configuration unchanged and show a plain-language reason.
- **FR-009**: The platform MUST refuse a connection whose phone number or business account is already linked to another tenant.
- **FR-010**: The platform MUST check the connection right after the flow by fetching the number's details from Meta. It MUST report "Connected" only if that check passes.

**Coexistence-specific**

- **FR-011**: In coexistence mode, the tenant MUST be able to keep sending and receiving on the WhatsApp Business app on their phone after connecting.
- **FR-012**: In coexistence mode, messages the tenant sends from the phone app MUST appear in the platform Inbox conversation for that customer, marked as sent from the phone, so the Inbox shows the full conversation.
- **FR-013**: In coexistence mode, the platform MUST NOT send its own automatic "your message will be redirected" reply to customers by default, because the tenant answers from the phone. The tenant can turn that reply back on.
- **FR-014**: In coexistence mode, the platform MUST NOT import past chat history from the phone app. The Inbox shows only messages exchanged from the moment of connection onward. If Meta offers a history-sharing step during onboarding, the platform declines it or ignores the data.

**Post-connection readiness**

- **FR-015**: After a successful automatic connection, the platform MUST show a "Submit standard templates" action. This action lists the platform's standard message templates (payment confirmation, subscription created, renewal, payment reminder, current balance, payment link, customer-reply alert, daily check-in, and the bulk outage/maintenance/feature/offer messages). Before submitting, the tenant reviews and can edit each one. Nothing is sent to Meta until the tenant confirms.
- **FR-015a**: In that review step, the tenant MUST be able to edit each template's wording (body text, and header/footer/button text where present) and choose which templates to submit.
- **FR-015b**: Each standard template's required variables (for example customer name, amount, balance, expiry date, payment-link token) MUST stay in the edited text. The platform MUST block submission of a template that is missing a required variable, or that adds a variable the platform does not fill. It MUST say which variable is missing or extra. Variables are shown as labeled placeholders (for example "{customer name}") rather than raw numbers, so the tenant can move them inside the sentence without breaking them.
- **FR-015c**: After submission, the Meta review status of each template (pending / approved / rejected with reason) MUST be visible, using the existing template management screen. A rejected template can be edited and resubmitted under the same variable rules.
- **FR-015d**: The same "Submit standard templates" action MUST also be available to manual-configuration tenants, with the same variable rules.
- **FR-016**: The platform MUST show whether the connected business account has a payment method set up in Meta, and warn when it does not.

**Manual configuration**

- **FR-017**: Manual configuration MUST keep working exactly as today, for new and existing tenants. The tenant brings their own Meta app and enters every field by hand: Phone Number ID, WhatsApp Business Account ID, App ID, App Secret, Access Token, API version and Webhook Verify Token. They also register the platform's webhook address in their own Meta app. Nothing is fetched automatically in this mode.
- **FR-018**: When this feature is released, existing manually configured tenants MUST be labeled "Manual configuration" automatically, with no action from them and no interruption to sending.

**Health, reconnect, disconnect**

- **FR-019**: Settings > Messaging MUST show, for an automatic connection: the method, the display number, the business name, the connection status (Connected / Needs attention / Disconnected), the quality rating, the messaging limit tier, and the date connected.
- **FR-020**: The platform MUST mark a connection "Needs attention" when Meta reports that access was revoked or the number was removed or unlinked, or when sends fail because of authorization. It MUST alert tenant admins inside the app.
- **FR-021**: The tenant MUST be able to reconnect by re-running the same automatic flow. This MUST replace the stored credentials only after the flow succeeds.
- **FR-022**: The tenant MUST be able to disconnect. This stops automatic sending, deletes the stored access credentials, unregisters webhook delivery for that business account where possible, and returns messaging to manual-link mode. Message history MUST stay.
- **FR-023**: Every connect, reconnect, method change and disconnect MUST be recorded with the time and the acting user, for support purposes.

### Key Entities

- **WhatsApp Connection (per tenant)**: the tenant's single active link to a WhatsApp number. Attributes: connection method (coexistence / new number / manual), status (Connected / Needs attention / Disconnected), Meta business ID and name, WhatsApp business account ID, phone number ID, display phone number, quality rating, messaging limit tier, payment-method-present flag, connected-at time, connected-by user, last health check time and result. Credentials are stored encrypted. This extends the tenant's existing WhatsApp settings rather than replacing them.
- **Connection Event**: an audit record of connect, reconnect, method change, disconnect, and health-status changes. Attributes: tenant, user, event type, time, outcome, and reason or error.
- **Platform Meta App** (platform-level, not per tenant): the single Meta app, owned by the platform operator, through which all tenants connect automatically. Manual-configuration tenants keep using their own app.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A tenant admin with an existing WhatsApp Business app number can go from "not connected" to sending a successful test message in under 5 minutes, without help from platform staff.
- **SC-002**: At least 80% of tenants who start an automatic connection finish it on their first attempt.
- **SC-003**: Platform staff time spent onboarding a tenant's WhatsApp drops from manual credential setup to zero for tenants using an automatic method.
- **SC-004**: All tenants configured manually before the release keep sending with zero failures caused by this feature.
- **SC-005**: A revoked or broken automatic connection shows "Needs attention" in Settings within 1 hour, or at the next send attempt, whichever is sooner.
- **SC-006**: Zero cases of one tenant's number or credentials being attached to another tenant.

## Assumptions

- **Platform Meta prerequisites**: the platform operator has, or will get, a verified Meta Business account and a Meta app approved as a WhatsApp Tech Provider (or Solution Partner), with the permissions needed to manage and message on behalf of customer business accounts, and with Embedded Signup and coexistence onboarding enabled. These are approved by Meta outside this codebase and can take days to weeks. Automatic methods cannot go live until they are done, but manual configuration is not affected.
- **Meta billing**: each tenant pays Meta for their own conversations through a payment method on their own business account (the standard default for Embedded Signup). The platform does not resell or pay for WhatsApp conversations in this feature.
- **One number per tenant**: each tenant has at most one active WhatsApp connection at a time, matching today's model.
- **Coexistence availability**: coexistence depends on Meta's eligibility rules (Business app version, country, number age), which the platform cannot control. Lebanon's eligibility will be confirmed during planning. If it is not supported, the new-number path and manual configuration remain.
- **Existing forwarding feature**: the "forward customer replies to a staff mobile" feature stays available in all methods. In coexistence it is usually unnecessary, because the phone already receives every message.
- **Reseller-level / platform-owned WhatsApp** (messages the platform sends to resellers) is out of scope. This feature covers only tenant-owned numbers.
- **Mobile layout**: the connection flow must work from a phone browser as well as desktop, since the Meta window supports both.
