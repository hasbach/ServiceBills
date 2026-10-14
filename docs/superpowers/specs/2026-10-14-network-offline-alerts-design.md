# Network offline / back-online alerts

Date: 2026-10-14. Scope: agent-mode tenants (DeltaNet). Direct mode is untouched.

## Problem

The owner asked whether the app can tell staff when the CCR, the OLT or ONUs go
offline or lose power. Before this change, device status was tracked and shown
(Network Devices / Network Tree), but nothing sent an alert. The cloud app on
Render reaches devices only through the on-prem agent, and the CCR was only
checked on a manual "Check Now".

## Design

Three alert sources, all delivered as web push on a new `network` topic to the
tenant's **admin** users, and all recorded in a new `network_alert_event` table.

1. **Agent / site offline** (checked by the 15-minute scheduled job
   `refresh_agent_mode_network_status`). If the agent's last poll is older than
   `AGENT_OFFLINE_ALERT_SECONDS` (5 min — the agent normally polls every few
   seconds, so a 5-minute gap is not a blip), push "agent offline" once and set
   `network_agent.offline_alerted_at`. When it is seen again, push "back online"
   once and clear it. A full power cut at the office looks exactly like this
   (the agent box, CCR and OLT all go dark together), so this is also the
   "site lost power" alert. An agent that never connected does not alert.

2. **Device down / recovered** (CCR and OLT). Evaluated where the agent posts a
   job result (`_stamp_device_status_from_agent`). The first non-`online` status
   (`unreachable`, `auth_failed`) stamps `network_device.failing_since`; a later
   failure at least `DEVICE_DOWN_CONFIRM_SECONDS` (10 min) after that pushes
   "device down" once and sets `network_device.alert_status = 'down'`. The first
   `online` result clears `failing_since` and, if a down alert was sent, pushes
   "back online". Time-based rather than a count because one scheduled tick
   posts two OLT results (olt_status + cpe_locations), so "two failures" could
   be one blip; this way one timed-out SNMP walk does not page anyone, and a real
   outage is reported on the next tick (~15–30 minutes).
   **Suppression:** while the agent is flagged offline (`offline_alerted_at` set),
   device alerts are not pushed and `alert_status` is not advanced, so the site
   alert is the only one staff get and the device alert can still fire later.
   The CCR is now added to the scheduled job (`device_health`), so it is checked
   unattended like the OLT.

3. **ONU mass drop** (per PON port). When an `olt_status` result is stored, it is
   diffed against the previous successful `olt_status` for that OLT. If, on one
   PON port, at least `ONU_MASS_DROP_MIN_COUNT` (3) ONUs that were online are now
   offline/missing AND they are at least `ONU_MASS_DROP_MIN_FRACTION` (50%) of
   that port's previously-online ONUs, push one alert for the port ("probable
   fibre/splitter cut or area power outage"). Active port alerts are kept in
   `network_device.onu_alert_state` so the same port does not alert again while
   it is still down; once at least half of the dropped ONUs are back online a
   single "recovered" push is sent. A single customer's ONU going offline is
   **not** pushed — it stays visible on the Network Tree only.

## Known limitation

The OLT's deregister reason (dying-gasp/power-off vs. fibre LOS) is not scraped
yet, so an ONU alert cannot say "powered off" vs. "fibre cut". The message says
"fibre/splitter cut or area power outage". Adding the reason column to the
vsol_olt walk is the follow-up that would let us split them.

## Not in scope

- WhatsApp delivery to a staff number: needs the owner's decision (which number,
  and a Meta-approved template, since these are business-initiated messages).
- An alert-history UI. Events are recorded and pruned after 90 days.
- Direct-mode tenants (no scheduled job runs for them).

## Schema (migration, additive)

- `network_device.alert_status` VARCHAR(10) NULL, `network_device.failing_since`
  TIMESTAMP NULL, `network_device.onu_alert_state` JSON NULL
- `network_agent.offline_alerted_at` TIMESTAMP NULL (hot table — lock-retry ALTER)
- `network_alert_event` (id, tenant_id, device_id SET NULL, kind, message, created_at)
