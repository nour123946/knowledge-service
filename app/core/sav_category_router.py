from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.sav import detect_sav_category
from app.llm.groq_llm import classify_sav_category_groq

logger = logging.getLogger(__name__)

SAV_CATEGORIES = {"delivery_issue", "exchange_return", "refund_cancel", "defective", "unknown"}


def build_sav_category_prompt(
    query: str,
    state: str = "idle",
    last_order_exists: bool = False,
    last_bot_message: str = "",
    history: Optional[List[Dict[str, Any]]] = None
) -> str:
    history = history or []
    history_text = ""
    for msg in history[-4:]:
        role = "Client" if msg.get("role") == "user" else "Bot"
        history_text += f"{role}: {(msg.get('content') or '').strip()}\\n"

    return f"""Tu classes la catégorie SAV d'un message utilisateur e-commerce.
Retourne UNIQUEMENT un JSON strict, sans texte autour:
{{"category":"delivery_issue|exchange_return|refund_cancel|defective|unknown","confidence":0.0,"reason":"..."}}

Règles:
- delivery_issue: suivi/tracking, où est ma commande/mon colis, pas encore arrivée, retard, non reçu, adresse de livraison (modifier adresse), problème de livraison.
    * IMPORTANT: suivi/tracking/où est ma commande/pas encore arrivée/retard => category="delivery_issue".
    * IMPORTANT: question générale de délai livraison (délai de livraison, combien de temps la livraison) => category="unknown" (question d'info/RAG, pas delivery_issue).
- exchange_return: échange, retour, changer d'article/taille/modèle.
    * IMPORTANT: si "annuler/annulé/stop" est lié à "échange/retour/taille" => category="exchange_return" (pas refund_cancel).
- refund_cancel: annulation, remboursement.
- defective: produit cassé, défectueux, abîmé, endommagé.
- unknown: demande SAV post-commande mais catégorie non claire, ou questions d'ETA/délai/quand.
- Si le message est vague (ex: "modifier ma commande", "changer ma commande") sans mention explicite d'adresse/livraison/suivi/colis/retard/non reçu/retour/échange/remboursement/défaut, retourne "unknown".
- Si le message est "modifier ma commande" (vague) => retourne "unknown".
- Si la question est une question de délai/ETA/quand générale => retourne "unknown" (pas delivery_issue).
- Ne réponds jamais "human" ici.

Contexte:
- state: {state}
- last_order_exists: {last_order_exists}
- last_bot_message: {last_bot_message or 'Aucun'}
- historique récent:
{history_text or 'Aucun'}

Message utilisateur:
{query}
"""


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    category = str(payload.get("category", "")).strip().lower()
    if category not in SAV_CATEGORIES:
        raise ValueError(f"Invalid SAV category: {category}")

    try:
        confidence = float(payload.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    reason = str(payload.get("reason", "")).strip() or "No reason provided"
    return {"category": category, "confidence": confidence, "reason": reason}


def classify_sav_category(
    query: str,
    state: str = "idle",
    last_order_exists: bool = False,
    last_bot_message: str = "",
    history: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    prompt = build_sav_category_prompt(
        query=query,
        state=state,
        last_order_exists=last_order_exists,
        last_bot_message=last_bot_message,
        history=history,
    )

    try:
        payload = classify_sav_category_groq(prompt)
        if not isinstance(payload, dict):
            raise ValueError("Groq SAV category payload must be a dict")
        return _normalize_payload(payload)
    except Exception as e:
        logger.warning(f"⚠️ SAV category fallback used: {e}")
        regex_category = detect_sav_category(query, last_bot_message)
        if regex_category:
            fallback = {
                "category": regex_category,
                "confidence": 0.68,
                "reason": "regex fallback after Groq failure"
            }
        else:
            fallback = {
                "category": "unknown",
                "confidence": 0.5,
                "reason": "Groq failure and regex no match"
            }
        normalized = _normalize_payload(fallback)
        logger.debug(f"🧩 SAV category fallback payload: {normalized}")
        return normalized
