#!/usr/bin/env python3
"""
Pytest scenarios for real-time customer updates across web, WhatsApp, and Facebook.
Covers:
- Order status + tracking notifications
- SAV status + admin message notifications
- Reactive customer Q&A from DB truth
- Delivery results recorded per channel
"""

from fastapi.testclient import TestClient
from datetime import datetime

import app.main as m
from app.core.database import get_database
from app.core.order_manager import OrderManager
from app.core.sav_tickets import create_or_update_ticket


def _cleanup(db, session_id: str):
    db["orders"].delete_many({"session_id": session_id})
    db["sav_tickets"].delete_many({"session_id": session_id})
    db["delivery_events"].delete_many({"recipient": session_id})
    db["admin_audit_logs"].delete_many({"resource_id": {"$regex": "^(CMD-|SAV-)"}})
    today = datetime.utcnow().strftime("%Y%m%d")
    db["orders"].delete_many({"order_id": {"$regex": f"^CMD-{today}-"}})


def _patch_outbound(monkeypatch, sent):
    monkeypatch.setattr(m, "send_whatsapp_message", lambda to, text, use_buttons=False: sent.append(("whatsapp", to, text)) or True)
    monkeypatch.setattr(m, "send_facebook_message", lambda recipient_id, text, quick_replies=None: sent.append(("facebook", recipient_id, text)) or True)


def test_order_updates_notify_and_answer(monkeypatch):
    client = TestClient(m.app)
    db = get_database()
    session_id = "33610000001"
    _cleanup(db, session_id)

    sent = []
    _patch_outbound(monkeypatch, sent)

    order_mgr = OrderManager()
    order = order_mgr.create_order(
        session_id=session_id,
        customer_info={"name": "Alice", "phone": session_id, "address": "Tunis"},
        cart_items=[{"product_name": "Puma RS-X", "price": 310, "quantity": 1}],
        payment_method="cash_on_delivery",
        channel="whatsapp",
    )

    token_resp = client.post(
        "/customer/access-token",
        json={"channel": "whatsapp", "customer_id": session_id},
    )
    assert token_resp.status_code == 200
    token = token_resp.json()["token"]

    status_resp = client.post(
        f"/admin/orders/{order['order_id']}/status",
        params={"status": "shipped", "note": "Packed and dispatched"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert status_resp.status_code == 200

    tracking_resp = client.post(
        f"/admin/orders/{order['order_id']}/tracking",
        params={"tracking_number": "TRK123456"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert tracking_resp.status_code == 200

    updates_resp = client.get(
        "/customer/updates",
        headers={"x-customer-token": token},
    )
    assert updates_resp.status_code == 200
    updates = updates_resp.json()["updates"]
    assert any(u["type"] == "order" for u in updates)
    assert updates_resp.json()["latest_order"]["tracking_number"] == "TRK123456"

    ask_resp = client.post(
        "/ask",
        json={"query": "où en est ma commande ?", "session_id": session_id, "channel": "whatsapp"},
    )
    assert ask_resp.status_code == 200
    answer = ask_resp.json()["final_answer"]
    assert "TRK123456" in answer or "Expédiée" in answer
    assert sent


def test_sav_updates_notify_and_answer(monkeypatch):
    client = TestClient(m.app)
    db = get_database()
    session_id = "fb_987654321"
    _cleanup(db, session_id)

    sent = []
    _patch_outbound(monkeypatch, sent)

    order_mgr = OrderManager()
    order = order_mgr.create_order(
        session_id=session_id,
        customer_info={"name": "Bob", "phone": "987654321", "address": "Sfax"},
        cart_items=[{"product_name": "Adidas Ultraboost", "price": 420, "quantity": 1}],
        payment_method="cash_on_delivery",
        channel="facebook",
    )

    ticket = create_or_update_ticket(
        session_id=session_id,
        channel="facebook",
        category="exchange_return",
        order_id=order["order_id"],
        summary="Exchange request",
        last_user_message="je veux échanger ma taille",
        status="open",
    )

    token_resp = client.post(
        "/customer/access-token",
        json={"channel": "facebook", "customer_id": "987654321"},
    )
    assert token_resp.status_code == 200
    token = token_resp.json()["token"]

    status_resp = client.put(
        f"/admin/sav-tickets/{ticket['ticket_id']}/status",
        params={"status": "in_progress", "reason": "Ticket accepted"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert status_resp.status_code == 200

    message_resp = client.post(
        f"/admin/sav-tickets/{ticket['ticket_id']}/message",
        params={"content": "Nous préparons votre échange."},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert message_resp.status_code == 200
    assert message_resp.json()["last_delivery"]["status"] in {"sent", "failed"}

    updates_resp = client.get(
        "/customer/updates",
        headers={"x-customer-token": token},
    )
    assert updates_resp.status_code == 200
    updates = updates_resp.json()["updates"]
    assert any(u["type"] == "sav" for u in updates)
    assert any(u["type"] == "message" for u in updates)

    public_replies = [u for u in updates if u.get("type") == "message" and u.get("message_type") == "public_reply"]
    assert public_replies, "Expected at least one customer-visible admin public_reply"
    assert any("Nous préparons votre échange." in (u.get("message") or "") for u in public_replies)
    assert any("Status:" in (u.get("message") or "") for u in public_replies)
    assert any((u.get("status") or "") == "in_progress" for u in public_replies)
    assert not any("we received your request" in (u.get("message") or "").lower() for u in updates)

    assert updates_resp.json()["latest_ticket"].get("status")
    assert "ticket_id" not in updates_resp.json()["latest_ticket"]

    ask_resp = client.post(
        "/ask",
        json={"query": "où en est mon SAV ?", "session_id": session_id, "channel": "facebook"},
    )
    assert ask_resp.status_code == 200
    answer = ask_resp.json()["final_answer"]
    assert ticket["ticket_id"] in answer or "En cours" in answer or "Dernier message" in answer
    assert sent


def test_customer_updates_public_session_and_internal_hidden(monkeypatch):
    client = TestClient(m.app)
    db = get_database()
    session_id = "web_public_updates_001"
    _cleanup(db, session_id)

    sent = []
    _patch_outbound(monkeypatch, sent)

    order_mgr = OrderManager()
    order = order_mgr.create_order(
        session_id=session_id,
        customer_info={"name": "Carol", "phone": "55112233", "address": "Tunis"},
        cart_items=[{"product_name": "Puma RS-X", "price": 310, "quantity": 1}],
        payment_method="cash_on_delivery",
        channel="web",
    )

    ticket = create_or_update_ticket(
        session_id=session_id,
        channel="web",
        category="delivery_issue",
        order_id=order["order_id"],
        summary="Address update",
        last_user_message="Je veux changer mon adresse",
        status="open",
    )

    internal_note_resp = client.post(
        f"/admin/sav-tickets/{ticket['ticket_id']}/note",
        params={"internal_note": "Internal QA note", "admin_action": "triage"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert internal_note_resp.status_code == 200

    order_status_resp = client.post(
        f"/admin/orders/{order['order_id']}/status",
        params={"status": "shipped", "note": "Dispatch confirmed"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert order_status_resp.status_code == 200

    resolution_resp = client.post(
        f"/admin/sav-tickets/{ticket['ticket_id']}/note",
        params={"resolution_note": "New delivery address has been confirmed.", "send_resolution_to_customer": "true"},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert resolution_resp.status_code == 200

    updates_resp = client.get(
        "/customer/updates",
        params={"session_id": session_id, "channel": "web"},
    )
    assert updates_resp.status_code == 200
    updates = updates_resp.json().get("updates", [])

    assert any(u.get("type") == "order" for u in updates)
    assert any(u.get("type") == "message" and "confirmed" in (u.get("message") or "").lower() for u in updates)
    assert not any("Internal QA note" in (u.get("message") or "") for u in updates)
    assert not any(u.get("message_type") == "internal" for u in updates)


def test_resolution_can_be_blank(monkeypatch):
    client = TestClient(m.app)
    db = get_database()
    session_id = "web_resolution_blank_001"
    _cleanup(db, session_id)

    sent = []
    _patch_outbound(monkeypatch, sent)

    order_mgr = OrderManager()
    order = order_mgr.create_order(
        session_id=session_id,
        customer_info={"name": "Dana", "phone": "55990011", "address": "Sousse"},
        cart_items=[{"product_name": "Adidas Ultraboost", "price": 420, "quantity": 1}],
        payment_method="cash_on_delivery",
        channel="web",
    )

    ticket = create_or_update_ticket(
        session_id=session_id,
        channel="web",
        category="refund_cancel",
        order_id=order["order_id"],
        summary="Refund request",
        last_user_message="Je veux annuler",
        status="open",
    )

    status_resp = client.post(
        f"/admin/sav-tickets/{ticket['ticket_id']}/status",
        params={"status": "resolved", "reason": ""},
        headers={"x-api-key": m.ADMIN_API_KEY, "x-admin-user": "ops"},
    )
    assert status_resp.status_code == 200

    updates_resp = client.get(
        "/customer/updates",
        params={"session_id": session_id, "channel": "web"},
    )
    assert updates_resp.status_code == 200
    updates = updates_resp.json().get("updates", [])
    assert any(u.get("type") == "sav" and u.get("status") == "resolved" for u in updates)

