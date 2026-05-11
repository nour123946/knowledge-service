# Operations Playbook (Orders + SAV)

## 1) Support dashboard workflow

1. Open `/widget/admin_dashboard.html`.
2. Authenticate with `x-api-key`.
3. Review SAV queue in this order:
   - `open`
   - `in_progress`
   - `waiting_customer`
4. Open ticket details and validate:
   - linked order (`order_id`)
   - `status_history`
   - `messages_thread`

## 2) Common SAV handling

### Delivery issue
- Set status to `in_progress`.
- Add internal note with investigation action.
- Send customer-facing message via `/admin/sav-tickets/{ticket_id}/message`.
- Resolve with reason when issue is confirmed fixed.

### Exchange / return
- Validate size mismatch and item condition from thread.
- Add note for warehouse action.
- Send customer update with expected timeline.

### Defective product
- Confirm evidence (photo/description).
- Add note for quality team.
- Send next-step message to customer.

### Refund / cancellation
- Confirm request scope (full/partial).
- Update status and add compliance note.
- Send customer confirmation message.

## 3) Verify customer communication delivery

- Each admin message generates a `delivery_events` record.
- Delivery status is reflected in ticket `messages_thread[*].delivery`:
  - `sent`
  - `failed`
- For failed statuses:
  1. Check channel token configuration (`/webhook/status`).
  2. Re-send message from dashboard endpoint.
  3. Track retry attempts and last error from `delivery_events`.

## 4) Secure customer access

Use read-only flow:
1. `POST /customer/access-token`
   - Option A: `channel + customer_id`
   - Option B: `order_id + phone_last4`
2. `GET /customer/updates` with `x-customer-token`

Do **not** expose admin endpoints to customers.

## 5) Audit and compliance

- All admin status/note/message actions are logged in `admin_audit_logs`.
- Use `GET /admin/audit-logs` for operations review.
- Logs include: actor, action, resource, reason, before/after snapshots, timestamp.

## 6) Incident quick checks

- No customer updates visible:
  - Verify `customer_identifier` exists on order/SAV ticket.
  - Re-issue customer access token.
- Admin message not delivered:
  - Inspect `delivery_events.status` and `error`.
  - Validate provider credentials.
- Inconsistent timeline:
  - Confirm updates came through status endpoints (not direct DB writes).
