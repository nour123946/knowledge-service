from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from app.core.escalation import detect_frustration, detect_human_request
from app.llm.groq_llm import route_intent_groq

logger = logging.getLogger(__name__)

ROUTES = {"order", "sav", "info", "human"}


def build_router_prompt(query: str, state: str = "idle", history: Optional[List[Dict[str, Any]]] = None) -> str:
    history = history or []
    history_text = ""
    for msg in history[-6:]:
        role = "Client" if msg.get("role") == "user" else "Bot"
        history_text += f"{role}: {(msg.get('content') or '').strip()}\n"

    return f"""Tu es un routeur d'intention pour un assistant e-commerce.
Retourne UNIQUEMENT un JSON strict, sans texte autour:
{{"route":"order|sav|info|human","confidence":0.0,"reason":"..."}}

Règles:
- human: demande explicite d'agent/conseiller/humain surtout, frustration, colère, escalade.
- order: intention d'achat/commande/passer commande/acheter/commander/finaliser/panier.
- sav: problème post-commande ou demande de modification/annulation/retour/échange/remboursement/SUIVI/ADRESSE/commande existante.
    * IMPORTANT: suivi/tracking/où est ma commande ou mon colis/pas encore arrivée/retard => route="sav".
    * IMPORTANT: Si la question est générale sur le DÉLAI DE LIVRAISON (délai livraison, combien de temps la livraison) => route="info", PAS sav.
- info: questions sur le magasin/boutique/localisation (magasin, boutique, point de vente, adresse du magasin, localisation, où êtes-vous) => route="info".
- Ne confonds pas "où est votre magasin" (info) avec "où est ma commande/mon colis" (sav).
- info: FAQ, prix, disponibilité, explications, how-to, questions non actionnables, et **DÉLAI/ETA DE LIVRAISON**.
- Si la demande est un how-to comme "comment faire..." sans action immédiate, choisis info.

Contexte état: {state}

Historique récent:
{history_text or 'Aucun'}

Message utilisateur:
{query}
"""


def _normalize_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    route = str(payload.get("route", "")).strip().lower()
    if route not in ROUTES:
        raise ValueError(f"Invalid route: {route}")

    try:
        confidence = float(payload.get("confidence", 0.0))
    except Exception:
        confidence = 0.0
    confidence = max(0.0, min(confidence, 1.0))

    reason = str(payload.get("reason", "")).strip() or "No reason provided"
    return {"route": route, "confidence": confidence, "reason": reason}


def _fallback_route(query: str, state: str = "idle") -> Dict[str, Any]:
    q = (query or "").lower()

    if detect_frustration(query) or detect_human_request(query) or any(x in q for x in ["agent", "conseiller", "humain", "humaine", "support"]):
        return {"route": "human", "confidence": 0.92, "reason": "human request or frustration detected"}

    # Localisation magasin/boutique => INFO (sauf si c'est clairement commande/colis/suivi)
    store_markers = ["magasin", "boutique", "point de vente", "adresse du magasin", "localisation", "où êtes-vous", "ou etes-vous", "ou etes vous"]
    order_tracking_markers = ["commande", "colis", "suivi", "tracking", "où est ma", "ou est ma", "où en est", "ou en est"]
    if any(marker in q for marker in store_markers) and not any(marker in q for marker in order_tracking_markers):
        return {"route": "info", "confidence": 0.94, "reason": "store location question => info"}

    # Suivi / tracking / retard -> SAV prioritaire
    tracking_markers = [
        "suivi", "tracking", "où est", "ou est", "où en est", "ou en est", "mon colis",
        "ma commande", "pas encore arrivée", "pas encore arrivee", "pas encore reçu", "pas encore recu",
        "retard", "en retard", "bloqué", "bloque"
    ]
    if any(marker in q for marker in tracking_markers):
        return {"route": "sav", "confidence": 0.93, "reason": "tracking/delay post-order request => sav"}

    # ETA/DÉLAI général: info (RAG)
    eta_markers = [
        "délai de livraison", "delai de livraison", "combien de temps la livraison",
        "combien de temps pour la livraison", "dans combien de temps la livraison",
        "eta livraison", "estimated delivery"
    ]
    if any(marker in q for marker in eta_markers):
        return {"route": "info", "confidence": 0.95, "reason": "ETA/delay question => info route"}

    sav_markers = [
        "retour", "échang", "echange", "rembours", "annul", "modifier", "changer", "adresse",
        "suivi", "tracking", "livraison", "colis", "reçu", "recu", "pas reçu", "pas recu",
        "non reçu", "non recu", "défect", "defect", "cass", "abîm", "abim", "problème", "probleme",
        "souci", "réclamation", "reclamation", "plainte"
    ]
    if any(marker in q for marker in sav_markers):
        return {"route": "sav", "confidence": 0.86, "reason": "post-order / support keywords detected"}

    order_markers = ["acheter", "commander", "commande", "panier", "finaliser", "valider", "je veux", "je voudrais", "je souhaite", "prendre"]
    if any(marker in q for marker in order_markers):
        return {"route": "order", "confidence": 0.82, "reason": "purchase intent detected"}

    if any(marker in q for marker in ["merci", "thanks", "thank you", "prix", "combien", "disponible", "comment", "c'est quoi", "qu'est-ce", "quels", "quelle", "où", "quand"]):
        return {"route": "info", "confidence": 0.78, "reason": "information request detected"}

    return {"route": "info", "confidence": 0.55, "reason": "default fallback to info"}


def route_intent(query: str, session_id: str = "default", state: str = "idle", history: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    prompt = build_router_prompt(query=query, state=state, history=history)

    try:
        payload = route_intent_groq(prompt)
        if not isinstance(payload, dict):
            raise ValueError("Groq router payload must be a dict")
        routed = _normalize_payload(payload)
        return routed
    except Exception as e:
        logger.warning(f"⚠️ Router fallback used for session={session_id}: {e}")
        fallback = _fallback_route(query, state=state)
        normalized_fallback = _normalize_payload(fallback)
        logger.debug(f"🧭 Router fallback payload: {normalized_fallback}")
        return normalized_fallback