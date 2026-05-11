# Validation Checklist - Real-time Customer Updates

## A. Order lifecycle updates
- [ ] Create order for each channel identity (web/whatsapp/facebook).
- [ ] Admin sets order status to `shipped`.
- [ ] Admin sets tracking number.
- [ ] Customer receives proactive update:
  - [ ] Web: visible in chat stream as `Support/Update`.
  - [ ] WhatsApp/Facebook: outbound sent.
- [ ] Customer asks `où en est ma commande` and gets latest status + tracking + last updated.

## B. SAV lifecycle updates
- [ ] Create SAV ticket from customer flow.
- [ ] Admin sets SAV status to `in_progress`.
- [ ] Admin sends SAV message.
- [ ] Customer receives proactive update:
  - [ ] Web: visible in chat stream as `Support/Update`.
  - [ ] WhatsApp/Facebook: outbound sent.
- [ ] Customer asks `où en est mon SAV` and gets latest status + last admin message + last updated.

## C. Security & access
- [ ] `GET /customer/updates` requires valid `x-customer-token`.
- [ ] Invalid/expired token is rejected.
- [ ] Token scoped by order when issued with `order_id`.
- [ ] No admin endpoints exposed to customer flows.

## D. Incremental + dedupe
- [ ] First call returns updates and `next_cursor`.
- [ ] Second call with `cursor` returns only new updates.
- [ ] No duplicate update entries for same event.

## E. Auditability
- [ ] `delivery_events` has rows for status/tracking/SAV updates.
- [ ] Delivery row has `status`, `attempts`, `error` fields.
- [ ] `admin_audit_logs` records corresponding admin actions.

## F. Automated tests
- [ ] Run: `pytest -q test_realtime_customer_updates.py`
- [ ] Run: `pytest -q test_e2e_orders_sav_ops.py`
- [ ] Run demo: `python demo_e2e_realtime_updates.py`
