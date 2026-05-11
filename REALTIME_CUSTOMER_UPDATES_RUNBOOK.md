# Real-time Customer Updates Runbook (Orders + SAV)

## Scope
- Channels: Web widget chat, WhatsApp, Facebook Messenger.
- Events pushed to customers:
  - Order status update
  - Order tracking update
  - SAV ticket status update
  - SAV admin message
- Read-only customer APIs (token-protected):
  - `POST /customer/access-token`
  - `GET /customer/updates?cursor=<iso_ts>`
  - `GET /customer/order-status`
  - `GET /customer/sav-status`

## Prerequisites
- API running (`uvicorn app.main:app --reload`).
- MongoDB reachable.
- For real outbound:
  - WhatsApp: `WHATSAPP_TOKEN`, `PHONE_NUMBER_ID`
  - Facebook: `FACEBOOK_PAGE_TOKEN`
- Admin key set: `ADMIN_API_KEY`.

## Channel identifiers
- Web: `session_id` from widget localStorage.
  - Limitation: session-bound demo identity, not cross-device.
- WhatsApp: phone number identifier.
- Facebook: `fb_{sender_id}` in app session; customer identifier exposed as `facebook:{sender_id}`.

## Operational flow
1. Customer has an order and/or SAV ticket in DB.
2. Admin applies update via admin endpoints.
3. Backend writes delivery audit in `delivery_events` with attempts, status, and error.
4. Outbound is sent:
   - Web: surfaced by widget polling `/customer/updates` and injected in chat stream as `Support/Update`.
   - WhatsApp/Facebook: sent via Meta APIs.
5. Customer asks status in chat:
   - Order question -> DB truth (+ tracking + last updated)
   - SAV question -> DB truth (+ last admin message + last updated)

## Quick commands
- Order status update:
  - `POST /admin/orders/{order_id}/status?status=shipped&note=Dispatch started`
- Tracking update:
  - `POST /admin/orders/{order_id}/tracking?tracking_number=TRK123`
- SAV status update:
  - `PUT /admin/sav-tickets/{ticket_id}/status?status=in_progress&reason=Taken by agent`
- SAV admin message:
  - `POST /admin/sav-tickets/{ticket_id}/message?content=Votre dossier avance`

Use headers:
- `x-api-key: <ADMIN_API_KEY>`
- `x-admin-user: <operator_name>`

## Troubleshooting
- No customer updates shown on web:
  - Verify token issuance (`/customer/access-token`) returns 200.
  - Verify widget has `session_id` matching existing customer data.
  - Check `delivery_events` and `/customer/updates` payload.
- WhatsApp/Facebook not receiving updates:
  - Check channel credentials and webhook status (`/webhook/status`).
  - Inspect `delivery_events.error` and `attempts`.
- Duplicate updates:
  - Confirm `cursor` usage in polling/client.

## Audit locations
- `delivery_events`: send attempts/status/error per channel.
- `admin_audit_logs`: admin action trail.
- `orders.status_history`, `sav_tickets.status_history`, `sav_tickets.messages_thread`: source of truth.
