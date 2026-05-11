#!/usr/bin/env python3
"""
Repeatable demo script for Orders + SAV operational flows.
"""

from fastapi.testclient import TestClient

import app.main as m
from app.core.database import get_database
from app.core.order_manager import OrderManager
from app.core.sav import build_sav_reply


def run_demo():
    client = TestClient(m.app)
    db = get_database()

    session_id = "21655999888"
    db["orders"].delete_many({"session_id": session_id})
    db["sav_tickets"].delete_many({"session_id": session_id})

    print("\n=== DEMO: Order + SAV Ops ===")

    order_mgr = OrderManager()
    order = order_mgr.create_order(
        session_id=session_id,
        customer_info={"name": "Demo User", "phone": session_id, "address": "Sfax"},
        cart_items=[{"product_name": "Converse Chuck Taylor", "price": 190.0, "quantity": 1}],
        payment_method="cash_on_delivery",
        channel="whatsapp",
    )
    print(f"[1] Order created: {order['order_id']}")

    first = build_sav_reply(
        category="delivery_issue",
        last_order=order,
        user_text="je veux changer l'adresse",
        session_id=session_id,
        channel="whatsapp",
    )
    second = build_sav_reply(
        category="delivery_issue",
        last_order=order,
        user_text="oui",
        last_bot_text=first,
        session_id=session_id,
        channel="whatsapp",
    )
    print(f"[2] Assistant SAV flow completed: {'transmets' in second.lower()}")

    ticket = db["sav_tickets"].find_one({"session_id": session_id})
    ticket_id = ticket["ticket_id"]
    print(f"[3] Ticket created: {ticket_id}")

    status_resp = client.post(
        f"/admin/sav-tickets/{ticket_id}/status",
        params={"status": "in_progress", "reason": "Ops triage complete"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "demo_ops"},
    )
    print(f"[4] Admin status update: {status_resp.status_code}")

    message_resp = client.post(
        f"/admin/sav-tickets/{ticket_id}/message",
        params={"content": "Nous avons bien pris en charge votre demande."},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "demo_ops"},
    )
    print(f"[5] Admin message update: {message_resp.status_code}")
    print(f"    Delivery: {message_resp.json().get('last_delivery', {})}")

    token_resp = client.post(
        "/customer/access-token",
        json={"channel": "whatsapp", "customer_id": session_id},
    )
    token = token_resp.json()["token"]
    updates_resp = client.get("/customer/updates", headers={"x-customer-token": token})

    updates = updates_resp.json().get("updates", [])
    print(f"[6] Customer updates available: {len(updates)} entries")
    if updates:
        print(f"    Last update: {updates[-1]}")

    audit_logs = client.get("/admin/audit-logs", headers={"x-api-key": m.ADMIN_API_KEY}).json()
    print(f"[7] Audit logs fetched: {audit_logs.get('total', 0)} entries")

    print("=== DEMO COMPLETE ===\n")


if __name__ == "__main__":
    run_demo()
