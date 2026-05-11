from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from app.core.database import get_database


ORDER_PUBLIC_MESSAGES = {
    "pending": "Your order has been confirmed.",
    "confirmed": "Your order is being prepared.",
    "shipped": "Your order has been shipped.",
    "delivered": "Your order has been delivered.",
    "cancelled": "Your order has been cancelled.",
    "canceled": "Your order has been cancelled.",
}

SAV_PUBLIC_MESSAGES = {
    "open": "We received your request.",
    "waiting_user": "We received your request.",
    "waiting_customer": "We received your request.",
    "in_progress": "A support agent is reviewing your request.",
    "resolved": "Your issue has been resolved.",
    "cancelled": "Your issue has been resolved.",
    "canceled": "Your issue has been resolved.",
}

TECHNICAL_MESSAGE_MARKERS = {
    "ticket created",
    "order created",
    "status updated",
    "tracking number set",
    "fallback",
    "confidence",
    "dashboard",
    "backend",
    "internal",
}

MEANINGFUL_ORDER_STATUSES = {"confirmed", "shipped", "delivered", "cancelled", "canceled"}
MEANINGFUL_SAV_STATUSES = {"open", "in_progress", "resolved", "waiting_customer", "cancelled", "canceled"}
CUSTOMER_VISIBLE_SAV_MESSAGE_TYPES = {"public_reply", "resolution", "system_update"}


def utcnow() -> datetime:
    return datetime.utcnow()


def _clean_phone(value: str) -> str:
    return "".join(ch for ch in (value or "") if ch.isdigit())


def build_customer_identifier(channel: str, session_id: str) -> str:
    channel_norm = (channel or "web").strip().lower()
    sid = (session_id or "").strip()

    if channel_norm == "whatsapp":
        sid = _clean_phone(sid)
        return f"whatsapp:{sid}"

    if channel_norm == "facebook":
        if sid.startswith("fb_"):
            sid = sid[3:]
        return f"facebook:{sid}"

    return f"web:{sid}"


def parse_customer_identifier(channel: str, customer_id: str) -> str:
    channel_norm = (channel or "web").strip().lower()
    customer_id = (customer_id or "").strip()

    if channel_norm == "whatsapp":
        customer_id = _clean_phone(customer_id)
    if channel_norm == "facebook" and customer_id.startswith("fb_"):
        customer_id = customer_id[3:]

    return f"{channel_norm}:{customer_id}"


def _token_secret() -> str:
    return os.getenv("CUSTOMER_ACCESS_SECRET") or os.getenv("ADMIN_API_KEY", "MY_SUPER_ADMIN_TOKEN_123")


def _b64_url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _b64_url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def issue_customer_token(payload: Dict[str, Any], ttl_minutes: int = 30) -> str:
    now = utcnow()
    body = dict(payload or {})
    body["iat"] = int(now.timestamp())
    body["exp"] = int((now + timedelta(minutes=max(1, ttl_minutes))).timestamp())

    data = _b64_url_encode(json.dumps(body, separators=(",", ":")).encode("utf-8"))
    signature = hmac.new(_token_secret().encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"{data}.{signature}"


def validate_customer_token(token: str) -> Dict[str, Any]:
    if not token or "." not in token:
        raise ValueError("Invalid token format")

    data, signature = token.split(".", 1)
    expected = hmac.new(_token_secret().encode("utf-8"), data.encode("utf-8"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("Invalid token signature")

    payload = json.loads(_b64_url_decode(data).decode("utf-8"))
    exp = int(payload.get("exp") or 0)
    if exp <= int(utcnow().timestamp()):
        raise ValueError("Token expired")

    return payload


def dt_to_iso(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if value is None:
        return ""
    return str(value)


def normalize_sav_status(status: str) -> str:
    mapping = {
        "open": "Ouvert",
        "waiting_user": "En attente client",
        "waiting_customer": "En attente client",
        "in_progress": "En cours de traitement",
        "resolved": "Résolu",
        "canceled": "Annulé",
        "cancelled": "Annulé",
    }
    return mapping.get((status or "").strip().lower(), status or "-")


def normalize_order_status(status: str) -> str:
    mapping = {
        "pending": "En attente",
        "confirmed": "Confirmée",
        "shipped": "Expédiée",
        "delivered": "Livrée",
        "cancelled": "Annulée",
        "canceled": "Annulée",
    }
    return mapping.get((status or "").strip().lower(), status or "-")


def _parse_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.utcfromtimestamp(float(value))
        except Exception:
            return None
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).replace(tzinfo=None)
        except Exception:
            try:
                return datetime.strptime(raw, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return None
    return None


def _format_timestamp(value: Any) -> str:
    parsed = _parse_datetime(value)
    return parsed.isoformat() if parsed else dt_to_iso(value)


def _is_technical_message(value: str) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return False
    return any(marker in text for marker in TECHNICAL_MESSAGE_MARKERS)


def _is_meaningful_order_status(status: str) -> bool:
    return (status or "").strip().lower() in MEANINGFUL_ORDER_STATUSES


def _is_meaningful_sav_status(status: str) -> bool:
    return (status or "").strip().lower() in MEANINGFUL_SAV_STATUSES


def _order_update_message(order: Dict[str, Any], history_item: Optional[Dict[str, Any]] = None) -> str:
    status = (history_item or {}).get("status") or order.get("status") or "pending"
    status_label = normalize_order_status(status)
    order_id = order.get("order_id", "-")
    parts = [f"Commande {order_id} : {status_label}"]

    tracking_number = order.get("tracking_number") or (history_item or {}).get("tracking_number")
    if tracking_number:
        parts.append(f"Tracking {tracking_number}")

    note = (history_item or {}).get("note") or ""
    if note and note.strip() and note.strip().lower() not in {"order created", "status updated"}:
        parts.append(note.strip())

    updated_at = (history_item or {}).get("changed_at") or order.get("updated_at") or order.get("created_at")
    parts.append(f"Mis à jour le {_format_timestamp(updated_at)}")
    return " • ".join(parts)


def _sav_update_message(ticket: Dict[str, Any], history_item: Optional[Dict[str, Any]] = None, admin_message: str = "") -> str:
    status = (history_item or {}).get("status") or ticket.get("status") or "open"
    status_label = normalize_sav_status(status)
    ticket_id = ticket.get("ticket_id", "-")
    parts = [f"SAV {ticket_id} : {status_label}"]

    last_admin_message = admin_message.strip() if admin_message else ""
    if not last_admin_message and history_item:
        last_admin_message = (history_item.get("reason") or history_item.get("note") or "").strip()
    if last_admin_message:
        parts.append(f"Dernier message : {last_admin_message}")

    updated_at = (history_item or {}).get("changed_at") or ticket.get("updated_at") or ticket.get("created_at")
    parts.append(f"Mis à jour le {_format_timestamp(updated_at)}")
    return " • ".join(parts)


def _status_at_timestamp(ticket: Dict[str, Any], ts: datetime) -> str:
    history = ticket.get("status_history") or []
    closest_status = (ticket.get("status") or "open").strip().lower()
    closest_time = None

    for item in history:
        item_status = (item.get("status") or "").strip().lower()
        if not item_status:
            continue
        item_ts = _parse_datetime(item.get("changed_at") or ticket.get("updated_at") or ticket.get("created_at"))
        if not item_ts:
            continue
        if item_ts <= ts and (closest_time is None or item_ts >= closest_time):
            closest_status = item_status
            closest_time = item_ts

    return closest_status


def get_latest_order_snapshot(customer_identifier: Optional[str], order_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    db = get_database()
    query: Dict[str, Any] = {}
    if customer_identifier:
        query["customer_identifier"] = customer_identifier
    if order_id:
        query["order_id"] = order_id
    return db["orders"].find_one(query, {"_id": 0}, sort=[("updated_at", -1)])


def get_latest_sav_snapshot(customer_identifier: Optional[str], order_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    db = get_database()
    query: Dict[str, Any] = {}
    if customer_identifier:
        query["customer_identifier"] = customer_identifier
    if order_id:
        query["order_id"] = order_id
    return db["sav_tickets"].find_one(query, {"_id": 0}, sort=[("updated_at", -1)])


def _build_order_updates(order: Dict[str, Any]) -> List[Dict[str, Any]]:
    updates: List[Dict[str, Any]] = []
    seen = set()
    history = order.get("status_history") or []
    for item in history:
        status = item.get("status") or order.get("status")
        if not _is_meaningful_order_status(status):
            continue
        ts = _parse_datetime(item.get("changed_at") or order.get("updated_at") or order.get("created_at"))
        if not ts:
            continue
        dedupe_key = f"order:{order.get('order_id')}:{ts.isoformat()}:{item.get('status')}:{item.get('note') or item.get('tracking_number') or ''}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        updates.append({
            "kind": "order_status",
            "resource_type": "order",
            "resource_id": order.get("order_id"),
            "order_id": order.get("order_id"),
            "customer_identifier": order.get("customer_identifier"),
            "channel": order.get("channel", "web"),
            "status": item.get("status") or order.get("status"),
            "status_label": normalize_order_status(item.get("status") or order.get("status")),
            "tracking_number": order.get("tracking_number") or item.get("tracking_number") or None,
            "timestamp": ts.isoformat(),
            "message": _order_update_message(order, item),
            "support_message": item.get("note") or item.get("message") or "",
            "author": item.get("changed_by") or "system",
            "message_type": "system_update",
            "dedupe_key": dedupe_key,
        })
    return updates


def _build_sav_updates(ticket: Dict[str, Any]) -> List[Dict[str, Any]]:
    updates: List[Dict[str, Any]] = []
    seen = set()

    for item in ticket.get("status_history") or []:
        status = item.get("status") or ticket.get("status")
        if not _is_meaningful_sav_status(status):
            continue
        ts = _parse_datetime(item.get("changed_at") or ticket.get("updated_at") or ticket.get("created_at"))
        if not ts:
            continue
        dedupe_key = f"sav_status:{ticket.get('ticket_id')}:{ts.isoformat()}:{item.get('status')}:{item.get('reason') or ''}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        updates.append({
            "kind": "sav_status",
            "resource_type": "sav_ticket",
            "resource_id": ticket.get("ticket_id"),
            "ticket_id": ticket.get("ticket_id"),
            "order_id": ticket.get("order_id"),
            "customer_identifier": ticket.get("customer_identifier"),
            "channel": ticket.get("channel", "web"),
            "status": item.get("status") or ticket.get("status"),
            "status_label": normalize_sav_status(item.get("status") or ticket.get("status")),
            "timestamp": ts.isoformat(),
            "message": _sav_update_message(ticket, item),
            "support_message": item.get("reason") or "",
            "author": item.get("changed_by") or "system",
            "message_type": "system_update",
            "dedupe_key": dedupe_key,
        })

    for msg in ticket.get("messages_thread") or []:
        role = (msg.get("role") or "").strip().lower()
        if role not in {"admin"}:
            continue
        message_type = (msg.get("message_type") or "public_reply").strip().lower()
        if message_type not in CUSTOMER_VISIBLE_SAV_MESSAGE_TYPES:
            continue
        visible_to_customer = bool(msg.get("visible_to_customer", True))
        if not visible_to_customer:
            continue
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        ts = _parse_datetime(msg.get("created_at") or msg.get("ts") or ticket.get("updated_at") or ticket.get("created_at"))
        if not ts:
            continue
        status_at_message = _status_at_timestamp(ticket, ts)
        delivery = msg.get("delivery") or {}
        dedupe_key = f"sav_message:{ticket.get('ticket_id')}:{ts.isoformat()}:{content}"
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        updates.append({
            "kind": "sav_message",
            "resource_type": "sav_ticket",
            "resource_id": ticket.get("ticket_id"),
            "ticket_id": ticket.get("ticket_id"),
            "order_id": ticket.get("order_id"),
            "customer_identifier": ticket.get("customer_identifier"),
            "channel": ticket.get("channel", "web"),
            "status": status_at_message,
            "status_label": normalize_sav_status(status_at_message),
            "timestamp": ts.isoformat(),
            "message": _sav_update_message(ticket, admin_message=content),
            "support_message": content,
            "author": msg.get("author") or role,
            "message_type": message_type,
            "delivery": delivery,
            "dedupe_key": dedupe_key,
        })

    return updates


def format_customer_update(update: Dict[str, Any]) -> str:
    kind = (update.get("kind") or "").strip().lower()
    if kind == "order_status":
        status = (update.get("status") or "").strip().lower()
        return ORDER_PUBLIC_MESSAGES.get(status, "Your order status has been updated.")

    if kind == "sav_status":
        status = (update.get("status") or "").strip().lower()
        status_label = update.get("status_label") or normalize_sav_status(status)
        custom_message = (update.get("support_message") or "").strip()
        if custom_message and not _is_technical_message(custom_message):
            return f"Support update\nStatus: {status_label}\n{custom_message}"
        return f"Support update\nVotre demande SAV est maintenant : {status_label}."

    if kind == "sav_message":
        support_message = (update.get("support_message") or "").strip()
        status_label = update.get("status_label") or normalize_sav_status(update.get("status"))
        if support_message:
            return f"Support Team\nStatus: {status_label}\n{support_message}"
        return "Support Team replied."

    if kind == "human_handoff":
        return "A team member will contact you shortly."

    return "You have a new update."


def _sanitize_public_update(update: Dict[str, Any]) -> Dict[str, Any]:
    kind = update.get("kind") or "update"
    message_type = update.get("message_type") or ("public_reply" if kind == "sav_message" else "system_update")
    formatted_message = format_customer_update(update)
    clean = {
        "kind": kind,
        "status": update.get("status") or "",
        "status_label": update.get("status_label") or "",
        "timestamp": update.get("timestamp") or "",
        "message": formatted_message,
        "message_type": message_type,
        "author": update.get("author") or ("support_team" if kind == "sav_message" else "system"),
        "raw_text": formatted_message if kind == "sav_message" else (update.get("support_message") or "").strip(),
        "visibility": "public",
    }
    return clean


def _sav_status_has_related_customer_reply(status_update: Dict[str, Any], all_updates: List[Dict[str, Any]]) -> bool:
    if (status_update.get("kind") or "") != "sav_status":
        return False

    ticket_id = status_update.get("ticket_id")
    status_value = (status_update.get("status") or "").strip().lower()
    status_ts = _parse_datetime(status_update.get("timestamp"))
    if not ticket_id or not status_ts:
        return False

    for candidate in all_updates:
        if (candidate.get("kind") or "") != "sav_message":
            continue
        if candidate.get("ticket_id") != ticket_id:
            continue
        candidate_type = (candidate.get("message_type") or "public_reply").strip().lower()
        if candidate_type not in CUSTOMER_VISIBLE_SAV_MESSAGE_TYPES:
            continue
        candidate_ts = _parse_datetime(candidate.get("timestamp"))
        if not candidate_ts:
            continue
        if (candidate.get("status") or "").strip().lower() != status_value:
            continue
        if 0 <= (candidate_ts - status_ts).total_seconds() <= 300:
            return True

    return False


def collect_customer_updates(
    customer_identifier: Optional[str],
    order_id: Optional[str] = None,
    since: Optional[Any] = None,
    include_internal: bool = False,
) -> Dict[str, Any]:
    db = get_database()
    since_dt = _parse_datetime(since)

    orders_query: Dict[str, Any] = {}
    sav_query: Dict[str, Any] = {}

    if customer_identifier:
        orders_query["customer_identifier"] = customer_identifier
        sav_query["customer_identifier"] = customer_identifier

    if order_id:
        orders_query["order_id"] = order_id
        sav_query["order_id"] = order_id

    orders = list(db["orders"].find(orders_query, {"_id": 0}))
    tickets = list(db["sav_tickets"].find(sav_query, {"_id": 0}))

    updates: List[Dict[str, Any]] = []
    for order in orders:
        updates.extend(_build_order_updates(order))

    for ticket in tickets:
        updates.extend(_build_sav_updates(ticket))

    if since_dt:
        updates = [u for u in updates if (_parse_datetime(u.get("timestamp")) or datetime.min) > since_dt]

    updates.sort(key=lambda x: x.get("timestamp") or "")

    deduped: List[Dict[str, Any]] = []
    seen_keys = set()
    for update in updates:
        key = update.get("dedupe_key") or f"{update.get('kind')}:{update.get('resource_id')}:{update.get('timestamp')}:{update.get('message')}"
        if key in seen_keys:
            continue
        seen_keys.add(key)
        item = dict(update)

        if include_internal:
            item["visibility"] = "internal"
            item["public_message"] = format_customer_update(item)
            deduped.append(item)
            continue

        if (item.get("kind") or "") == "sav_status" and _sav_status_has_related_customer_reply(item, updates):
            continue

        source_message = (item.get("support_message") or item.get("message") or "").strip()
        if _is_technical_message(source_message):
            item["support_message"] = ""

        deduped.append(_sanitize_public_update(item))

    next_cursor = deduped[-1]["timestamp"] if deduped else (since_dt.isoformat() if since_dt else "")

    return {
        "customer_identifier": customer_identifier,
        "order_id": order_id,
        "orders": orders,
        "tickets": tickets,
        "updates": deduped,
        "next_cursor": next_cursor,
    }


def log_admin_action(
    action: str,
    resource_type: str,
    resource_id: str,
    admin_user: str,
    reason: str = "",
    before: Optional[Dict[str, Any]] = None,
    after: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    db = get_database()
    db["admin_audit_logs"].insert_one({
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "admin_user": admin_user or "admin",
        "reason": reason or "",
        "before": before or {},
        "after": after or {},
        "metadata": metadata or {},
        "created_at": utcnow(),
    })


