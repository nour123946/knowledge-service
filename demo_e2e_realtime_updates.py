#!/usr/bin/env python3
"""
E2E demo script: real-time customer updates for Orders + SAV (web/whatsapp/facebook).
Run with API server up at http://127.0.0.1:8000.
"""

import requests

BASE_URL = "http://127.0.0.1:8000"
API_KEY = "MY_SUPER_ADMIN_TOKEN_123"


def post(path, **kwargs):
    r = requests.post(f"{BASE_URL}{path}", **kwargs)
    r.raise_for_status()
    return r.json()


def put(path, **kwargs):
    r = requests.put(f"{BASE_URL}{path}", **kwargs)
    r.raise_for_status()
    return r.json()


def get(path, **kwargs):
    r = requests.get(f"{BASE_URL}{path}", **kwargs)
    r.raise_for_status()
    return r.json()


def run_channel_demo(channel: str, session_id: str):
    print(f"\n=== DEMO {channel.upper()} ({session_id}) ===")

    # 1) Create order quickly via direct DB-backed flow (through chat can be longer)
    ask = post(
        "/ask",
        json={
            "query": "je veux commander puma",
            "session_id": session_id,
            "channel": channel,
            "conversation_state": "idle",
        },
    )
    print("ASK 1:", ask.get("final_answer", "")[:120])

    # Create token for customer read-only updates
    if channel == "web":
        token_payload = {"channel": "web", "customer_id": session_id}
    elif channel == "whatsapp":
        token_payload = {"channel": "whatsapp", "customer_id": session_id}
    else:
        token_payload = {"channel": "facebook", "customer_id": session_id.replace("fb_", "")}

    token_res = post("/customer/access-token", json=token_payload)
    token = token_res["token"]

    # 2) Find latest order from admin list filtered by session
    orders = get("/admin/orders", params={"limit": 20}, headers={"x-api-key": API_KEY}).get("orders", [])
    order = next((o for o in orders if o.get("session_id") == session_id), None)
    if not order:
        print("No existing order found for this session. Continue chat flow to create one first.")
        return

    order_id = order["order_id"]
    print("Order:", order_id)

    # 3) Admin updates order status + tracking (proactive notifications should be emitted)
    post(
        f"/admin/orders/{order_id}/status",
        params={"status": "shipped", "note": "Dispatch started"},
        headers={"x-api-key": API_KEY, "x-admin-user": "ops_demo"},
    )
    post(
        f"/admin/orders/{order_id}/tracking",
        params={"tracking_number": "TRK-DEMO-001"},
        headers={"x-api-key": API_KEY, "x-admin-user": "ops_demo"},
    )

    # 4) Customer fetches incremental updates
    updates_1 = get("/customer/updates", headers={"x-customer-token": token})
    print("Updates count:", len(updates_1.get("updates", [])))
    print("Latest order snapshot:", updates_1.get("latest_order", {}).get("status"), updates_1.get("latest_order", {}).get("tracking_number"))

    # 5) Customer asks reactive order status
    ask_status = post(
        "/ask",
        json={"query": "où en est ma commande ?", "session_id": session_id, "channel": channel},
    )
    print("Order status answer:", ask_status.get("final_answer", "")[:180])


def run_sav_demo(channel: str, session_id: str):
    print(f"\n=== SAV DEMO {channel.upper()} ({session_id}) ===")

    # Create SAV flow entry
    ask_sav = post(
        "/ask",
        json={"query": "je veux un échange", "session_id": session_id, "channel": channel},
    )
    print("SAV ask:", ask_sav.get("final_answer", "")[:120])

    # Token
    if channel == "web":
        token_payload = {"channel": "web", "customer_id": session_id}
    elif channel == "whatsapp":
        token_payload = {"channel": "whatsapp", "customer_id": session_id}
    else:
        token_payload = {"channel": "facebook", "customer_id": session_id.replace("fb_", "")}

    token = post("/customer/access-token", json=token_payload)["token"]

    # Get latest ticket
    tickets = get("/admin/sav-tickets", params={"limit": 20}, headers={"x-api-key": API_KEY}).get("tickets", [])
    ticket = next((t for t in tickets if t.get("session_id") == session_id), None)
    if not ticket:
        print("No SAV ticket found for this session.")
        return

    ticket_id = ticket["ticket_id"]
    print("Ticket:", ticket_id)

    # Admin status + message
    put(
        f"/admin/sav-tickets/{ticket_id}/status",
        params={"status": "in_progress", "reason": "Support took ownership"},
        headers={"x-api-key": API_KEY, "x-admin-user": "ops_demo"},
    )
    post(
        f"/admin/sav-tickets/{ticket_id}/message",
        params={"content": "Votre demande SAV est en cours de traitement."},
        headers={"x-api-key": API_KEY, "x-admin-user": "ops_demo"},
    )

    updates = get("/customer/updates", headers={"x-customer-token": token})
    print("SAV updates count:", len(updates.get("updates", [])))
    print("Latest SAV snapshot:", updates.get("latest_ticket", {}).get("status"))

    ask_status = post(
        "/ask",
        json={"query": "où en est mon SAV ?", "session_id": session_id, "channel": channel},
    )
    print("SAV status answer:", ask_status.get("final_answer", "")[:180])


if __name__ == "__main__":
    print("Real-time customer updates demo")

    # Web (uses localStorage session in widget; here simulated with explicit id)
    run_channel_demo("web", "web_demo_rt_001")
    run_sav_demo("web", "web_demo_rt_001")

    # WhatsApp simulation identifier = phone
    run_channel_demo("whatsapp", "33619990001")
    run_sav_demo("whatsapp", "33619990001")

    # Facebook simulation identifier = fb_<sender_id>
    run_channel_demo("facebook", "fb_99887766")
    run_sav_demo("facebook", "fb_99887766")

    print("\nDemo complete.")
