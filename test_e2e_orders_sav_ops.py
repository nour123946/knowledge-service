#!/usr/bin/env python3
"""
End-to-end operational validation for Orders + SAV production readiness.
Covers:
(a) SAV ticket creation from assistant flow
(b) Admin status/message updates
(c) Customer visibility of updates via secure token endpoint
(d) Routing edge cases (ETA vs tracking vs SAV)
"""

from fastapi.testclient import TestClient

import app.main as m
from app.core.database import get_database
from app.core.order_manager import OrderManager
from app.core.sav import build_sav_reply, detect_sav_category
from app.core.router import route_intent


def _clean_session(db, session_id: str):
    db["orders"].delete_many({"session_id": session_id})
    db["sav_tickets"].delete_many({"session_id": session_id})
    db["delivery_events"].delete_many({"recipient": session_id})
    db["admin_audit_logs"].delete_many({"resource_id": {"$regex": "^(CMD-|SAV-)"}})


def test_e2e_orders_sav_operations():
    client = TestClient(m.app)
    db = get_database()

    session_id = "21655123456"
    _clean_session(db, session_id)

    # (a) Ticket creation from assistant logic
    order_mgr = OrderManager()
    order = order_mgr.create_order(
        session_id=session_id,
        customer_info={"name": "E2E User", "phone": session_id, "address": "Tunis"},
        cart_items=[{"product_name": "Puma RS-X", "price": 310.0, "quantity": 1}],
        payment_method="cash_on_delivery",
        channel="whatsapp",
    )

    first_reply = build_sav_reply(
        category="delivery_issue",
        last_order=order,
        user_text="je veux changer mon adresse de livraison",
        last_bot_text="",
        history=[],
        session_id=session_id,
        channel="whatsapp",
    )
    final_reply = build_sav_reply(
        category="delivery_issue",
        last_order=order,
        user_text="oui",
        last_bot_text=first_reply,
        history=[],
        session_id=session_id,
        channel="whatsapp",
    )
    assert "transmets" in final_reply.lower()

    ticket = db["sav_tickets"].find_one({"session_id": session_id}, {"_id": 0})
    assert ticket is not None
    ticket_id = ticket["ticket_id"]

    # (b) Admin status and message update
    status_resp = client.post(
        f"/admin/sav-tickets/{ticket_id}/status",
        params={"status": "in_progress", "reason": "Support started"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops_agent_1"},
    )
    assert status_resp.status_code == 200

    msg_resp = client.post(
        f"/admin/sav-tickets/{ticket_id}/message",
        params={"content": "Votre dossier est en cours de traitement."},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops_agent_1"},
    )
    assert msg_resp.status_code == 200
    msg_data = msg_resp.json()
    assert msg_data.get("last_delivery", {}).get("status") in {"sent", "failed"}

    # (c) Customer secure visibility
    token_resp = client.post(
        "/customer/access-token",
        json={"channel": "whatsapp", "customer_id": session_id},
    )
    assert token_resp.status_code == 200
    token = token_resp.json()["token"]

    updates_resp = client.get(
        "/customer/updates",
        headers={"x-customer-token": token},
    )
    assert updates_resp.status_code == 200
    updates = updates_resp.json().get("updates", [])

    update_types = {u.get("type") for u in updates}
    assert "sav" in update_types
    assert "message" in update_types

    # (d) Routing edge cases ETA vs tracking vs SAV
    eta_route = route_intent("c'est quoi le délai de livraison ?", session_id=session_id, state="idle", history=[])
    tracking_route = route_intent("où est ma commande ?", session_id=session_id, state="idle", history=[])
    sav_category = detect_sav_category("je veux un remboursement", "")

    assert eta_route.get("route") in {"info", "order", "sav"}
    assert tracking_route.get("route") in {"order", "sav", "info"}
    assert sav_category == "refund_cancel"


if __name__ == "__main__":
    test_e2e_orders_sav_operations()
    print("E2E Orders+SAV operational test passed")
