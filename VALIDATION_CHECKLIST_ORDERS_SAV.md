# Validation Checklist — Orders + SAV Operations

## A. Ticket lifecycle
- [ ] Assistant flow creates SAV ticket with `ticket_id` and `customer_identifier`
- [ ] Ticket includes `status_history` from creation
- [ ] Ticket includes `messages_thread` with author + timestamp

## B. Admin operations
- [ ] Status update endpoint appends `status_history`
- [ ] Admin note/action updates are persisted
- [ ] Admin message endpoint creates delivery event
- [ ] Delivery status captured as `sent` or `failed`

## C. Customer visibility (secure)
- [ ] `/customer/access-token` works via `channel + customer_id`
- [ ] `/customer/access-token` works via `order_id + phone_last4`
- [ ] `/customer/updates` returns formatted updates (status, timestamp, support_message)
- [ ] Admin endpoints remain API-key protected

## D. Data integrity + auditability
- [ ] Order `status_history` includes actor + timestamp + note
- [ ] SAV `status_history` includes actor + timestamp + reason
- [ ] `admin_audit_logs` contains status, note, message, tracking actions

## E. Routing edge cases
- [ ] ETA-style question does not break order flow
- [ ] Tracking-style question remains distinguishable from generic SAV request
- [ ] Explicit SAV intent routes into SAV category logic

## F. Operational readiness
- [ ] Demo script runs end-to-end: `python demo_e2e_orders_sav.py`
- [ ] E2E test passes: `python test_e2e_orders_sav_ops.py`
- [ ] Playbook reviewed by support team
