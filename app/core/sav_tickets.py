from __future__ import annotations

from datetime import datetime
from typing import Optional, Dict, Any
import uuid

from app.core.database import get_database


def _now():
    return datetime.utcnow()


def get_sav_collection():
    db = get_database()
    return db["sav_tickets"]


def get_open_ticket(session_id: str) -> Optional[Dict[str, Any]]:
    col = get_sav_collection()
    return col.find_one({"session_id": session_id, "status": "open"})


def create_or_get_open_ticket(
    session_id: str,
    channel: str,
    category: str,
    order_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Option A: max 1 ticket OPEN par session.
    Si ticket open existe, on le réutilise (même si catégorie change).
    """
    col = get_sav_collection()
    existing = get_open_ticket(session_id)
    if existing:
        # Met à jour catégorie si différente (optionnel mais pratique)
        updates = {"updated_at": _now()}
        if category and existing.get("category") != category:
            updates["category"] = category
        if order_id and not existing.get("order_id"):
            updates["order_id"] = order_id

        col.update_one({"_id": existing["_id"]}, {"$set": updates})
        return col.find_one({"_id": existing["_id"]})

    # Créer un ticket
    ticket = {
        "ticket_id": f"SAV-{datetime.utcnow().strftime('%Y%m%d')}-{session_id[-6:]}",
        "session_id": session_id,
        "channel": channel,
        "category": category,
        "status": "open",  # open | waiting_user | in_progress | resolved
        "order_id": order_id,
        "summary": "",
        "details": {},
        "messages": [],
        "created_at": _now(),
        "updated_at": _now(),
    }
    col.insert_one(ticket)
    return col.find_one({"ticket_id": ticket["ticket_id"]})


def append_ticket_message(ticket_id: str, role: str, text: str, meta: Optional[Dict[str, Any]] = None) -> None:
    col = get_sav_collection()
    col.update_one(
        {"ticket_id": ticket_id},
        {
            "$push": {"messages": {"role": role, "text": text, "meta": meta or {}, "ts": _now()}},
            "$set": {"updated_at": _now()},
        }
    )


def update_ticket(ticket_id: str, patch: Dict[str, Any]) -> None:
    col = get_sav_collection()
    patch = dict(patch or {})
    patch["updated_at"] = _now()
    col.update_one({"ticket_id": ticket_id}, {"$set": patch})


def create_or_update_ticket(
    session_id: str,
    channel: str,
    category: str,
    order_id: Optional[str],
    summary: str = "",
    last_user_message: str = "",
    status: str = "open",
) -> Dict[str, Any]:
    """
    Crée ou met à jour un ticket SAV actif lié à la session/commande/catégorie.
    Schéma minimal visé:
      {ticket_id, order_id, category, status, created_at, updated_at, summary, last_user_message}
    """
    col = get_sav_collection()

    active_statuses = ["open", "in_progress"]
    existing = col.find_one(
        {
            "session_id": session_id,
            "order_id": order_id,
            "category": category,
            "status": {"$in": active_statuses},
        },
        sort=[("updated_at", -1)],
    )

    now = _now()
    if existing:
        patch = {
            "updated_at": now,
            "status": status or existing.get("status", "open"),
            "summary": summary or existing.get("summary", ""),
            "last_user_message": last_user_message or existing.get("last_user_message", ""),
            "channel": channel or existing.get("channel", "web"),
        }
        col.update_one({"_id": existing["_id"]}, {"$set": patch})
        return col.find_one({"_id": existing["_id"]})

    now = _now()
    ticket = {
        "ticket_id": f"SAV-{datetime.utcnow().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}",
        "session_id": session_id,
        "channel": channel,
        "order_id": order_id,
        "category": category,
        "status": status or "open",  # open | in_progress | waiting_customer | resolved | canceled
        "summary": summary,
        "last_user_message": last_user_message,
        "internal_note": "",
        "admin_action": "",
        "status_history": [{
            "status": status or "open",
            "changed_at": now,
            "changed_by": "system",
            "reason": "Ticket created"
        }],
        "messages_thread": [],
        "created_at": now,
        "updated_at": now,
        # Compat ascendante
        "details": {},
        "messages": [],
    }
    col.insert_one(ticket)
    return col.find_one({"ticket_id": ticket["ticket_id"]})


def cancel_exchange_ticket(session_id: str, order_id: Optional[str], last_user_message: str = "") -> Optional[Dict[str, Any]]:
    """
    Annule le ticket d'échange/retour actif s'il existe.
    """
    col = get_sav_collection()
    query: Dict[str, Any] = {
        "session_id": session_id,
        "category": "exchange_return",
        "status": {"$in": ["open", "in_progress"]},
    }
    if order_id:
        query["order_id"] = order_id

    existing = col.find_one(query, sort=[("updated_at", -1)])
    if not existing:
        return None

    now = _now()
    col.update_one(
        {"_id": existing["_id"]},
        {
            "$set": {
                "status": "canceled",
                "last_user_message": last_user_message or existing.get("last_user_message", ""),
                "updated_at": now,
            },
            "$push": {
                "status_history": {
                    "status": "canceled",
                    "changed_at": now,
                    "changed_by": "user",
                    "reason": "User canceled exchange/return"
                }
            }
        },
    )
    return col.find_one({"_id": existing["_id"]})


def update_sav_ticket_status(ticket_id: str, new_status: str, reason: str = "", changed_by: str = "admin") -> Optional[Dict[str, Any]]:
    """
    Déplace un ticket SAV vers un nouveau statut avec historique.
    Statuts valides: open | in_progress | waiting_customer | resolved | canceled
    """
    col = get_sav_collection()
    now = _now()
    
    result = col.update_one(
        {"ticket_id": ticket_id},
        {
            "$set": {
                "status": new_status,
                "updated_at": now
            },
            "$push": {
                "status_history": {
                    "status": new_status,
                    "changed_at": now,
                    "changed_by": changed_by,
                    "reason": reason
                }
            }
        }
    )
    
    if result.modified_count > 0:
        return col.find_one({"ticket_id": ticket_id})
    return None


def add_sav_ticket_note(ticket_id: str, note: str, action: str = "") -> Optional[Dict[str, Any]]:
    """
    Ajoute une note interne et/ou action au ticket.
    """
    col = get_sav_collection()
    patch = {"updated_at": _now()}
    
    if note:
        patch["internal_note"] = note
    if action:
        patch["admin_action"] = action
    
    col.update_one({"ticket_id": ticket_id}, {"$set": patch})
    return col.find_one({"ticket_id": ticket_id})


def add_sav_ticket_message(ticket_id: str, role: str, content: str, created_at: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    """
    Ajoute un message au thread d'un ticket (user | bot | admin).
    """
    col = get_sav_collection()
    now = created_at or _now()
    
    col.update_one(
        {"ticket_id": ticket_id},
        {
            "$push": {
                "messages_thread": {
                    "role": role,
                    "content": content,
                    "created_at": now
                }
            },
            "$set": {"updated_at": _now()}
        }
    )
    return col.find_one({"ticket_id": ticket_id})