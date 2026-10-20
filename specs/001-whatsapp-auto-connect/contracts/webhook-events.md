# Webhook Event Contract (inbound from Meta)

Endpoint: `POST /api/whatsapp/webhook`. The signature check (`X-Hub-Signature-256`, HMAC-SHA256 of the raw body) runs **before** any processing. The key comes from `wa_app_identity(resolved_tenant)` (research R-5). A bad signature returns 401. Every other path returns 200, so that Meta does not retry storms.

## Tenant resolution

| `changes[].field` | Resolve by | Then |
|---|---|---|
| `messages` (existing) | `value.metadata.phone_number_id` | existing inbound and status handling |
| `message_template_status_update` (existing) | `entry.id` = WABA id | existing |
| `smb_message_echoes` (new) | `value.metadata.phone_number_id` | §1 |
| `account_update` (new) | `value.waba_info.waba_id`, else `entry.id` | §2 |
| `phone_number_quality_update` (new) | `value.display_phone_number` matched within the WABA `entry.id`, else `entry.id` alone | §3 |
| `history`, `smb_app_state_sync` | — | log at debug, return 200, no processing (FR-014) |

A tenant with `connection_status='disconnected'` is treated as unmatched and skipped.

## §1 `smb_message_echoes`

`value.message_echoes[]` items look like `{from, to, id, timestamp, type, text{body} | image{...} | …, edit?{original_message_id, message}, revoke?{original_message_id}}`.

| Case | Action |
|---|---|
| Plain echo | Upsert the conversation for `to`, then `record_outbound(sender='phone', wa_message_id=id, msg_type=type, text=…)`. Media echoes store `wa_media_id`; the download runs on the same path as inbound media. Set `conversation.ai_paused=True`. No auto-reply, AI agent or ticket. |
| `edit` | Find the message by `original_message_id`, replace its `text`, set `status='edited'` |
| `revoke` | Find the message by `original_message_id`, set `status='revoked'` |
| Duplicate `id` | Ignored (unique constraint) |

## §2 `account_update`

| `value.event` | New status | Alert admins |
|---|---|---|
| `PARTNER_APP_UNINSTALLED` | needs_attention ("Access to WhatsApp was removed in Meta. Reconnect.") | yes |
| `PARTNER_REMOVED` | needs_attention (reason from `disconnection_info.reason`, e.g. PRIMARY_INACTIVITY → "WhatsApp Business app not opened for ~14 days") | yes |
| `ACCOUNT_DELETED`, `DISABLED_UPDATE` | needs_attention | yes |
| `ACCOUNT_OFFBOARDED` | needs_attention ("Phone app re-registered — waiting for reconnection") | only if not followed by `ACCOUNT_RECONNECTED` within 15 min (checked by the hourly job) |
| `ACCOUNT_RECONNECTED` | connected | info only |
| `ACCOUNT_RESTRICTION`, `ACCOUNT_VIOLATION` | unchanged; set `status_reason` | yes |
| `PARTNER_ADDED`, `PARTNER_APP_INSTALLED`, others | none; log only | no |

Each status change writes a `WhatsAppConnectionEvent` with `source='webhook'` and `detail` set to the event name and reason.

## §3 `phone_number_quality_update`

Update `quality_rating` and `messaging_limit_tier` from the payload (`current_limit`, `event`). The status is unchanged. If the event is `FLAGGED` or `DOWNGRADE`, alert the admins.
