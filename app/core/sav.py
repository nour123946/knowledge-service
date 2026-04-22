from __future__ import annotations

import re
from typing import Optional, Literal, Dict, Any, List, Tuple

SavCategory = Literal["delivery_issue", "exchange_return", "defective", "refund_cancel"]

# Déclencheurs "intention" (pas des données)
_DELIVERY_INTENT = [
    r"pas\s+reçu",
    r"pas\s+encore\s+reçu",
    r"pas\s+recu",
    r"pas\s+encore\s+recu",
    r"non\s+reçu",
    r"non\s+recu",
    r"retard",
    r"suivi",
    r"tracking",
    r"où\s+est",
    r"ou\s+est",
    r"bloqu",
    r"coinc",
    r"changer\s+l[’']adresse",
    r"modifier\s+l[’']adresse",
    r"nouvelle\s+adresse",
    r"adresse\s+de\s+livraison"
]
_EXCHANGE_INTENT = [r"échang", r"echange", r"retour", r"changer", r"remplacer", r"ne\s+me\s+va\s+pas"]
_DEFECT_INTENT = [r"défect", r"defect", r"cass", r"abîm", r"abim", r"endommag", r"déchir", r"dechir", r"qualité", r"qualite"]
_REFUND_INTENT = [r"rembours", r"annul", r"annulation", r"remboursement"]


def _is_cancel_exchange_intent(text: str) -> bool:
    t = (text or "").lower()
    cancel_markers = ["annuler", "annulé", "annule", "stop", "laisser tomber"]
    exchange_markers = ["échange", "echange", "retour", "taille"]
    return any(k in t for k in cancel_markers) and any(k in t for k in exchange_markers)

def _match_any(patterns: List[str], text: str) -> bool:
    t = (text or "").lower()
    return any(re.search(p, t) for p in patterns)

def detect_sav_category_from_text(text: str) -> Optional[SavCategory]:
    """Détecte la catégorie SAV à partir du message actuel (intention)."""
    t = (text or "").lower().strip()

    # Cas spécial: "annuler l'échange/retour" => reste exchange_return (pas refund_cancel)
    if _is_cancel_exchange_intent(t):
        return "exchange_return"

    # Priorité : défaut > remboursement > échange > livraison
    if _match_any(_DEFECT_INTENT, t):
        return "defective"
    if _match_any(_REFUND_INTENT, t):
        return "refund_cancel"
    if _match_any(_EXCHANGE_INTENT, t):
        return "exchange_return"
    if _match_any(_DELIVERY_INTENT, t):
        return "delivery_issue"
    return None

def infer_sav_category_from_context(last_bot_text: str) -> Optional[SavCategory]:
    """Déduit la catégorie SAV si le user répond juste par 'oui', '38/39', etc."""
    t = (last_bot_text or "").lower()

    if any(k in t for k in ["taille reçue", "taille recue", "taille souhaitée", "taille souhaitee", "échange", "echange", "retour"]):
        return "exchange_return"
    if any(k in t for k in ["adresse", "livraison", "colis", "suivi", "tracking"]):
        return "delivery_issue"
    if any(k in t for k in ["photo", "défaut", "defaut", "endommag", "cass", "abîm", "abim"]):
        return "defective"
    if any(k in t for k in ["annulation", "annuler", "remboursement", "rembourser"]):
        return "refund_cancel"

    return None

def detect_sav_category(user_text: str, last_bot_text: str = "") -> Optional[SavCategory]:
    """
    Détecte SAV soit via intention (message actuel), soit via contexte (dernier message bot).
    """
    cat = detect_sav_category_from_text(user_text)
    if cat:
        return cat
    return infer_sav_category_from_context(last_bot_text)

def format_order_items(order: Dict[str, Any], max_items: int = 3) -> str:
    items = order.get("items") or []
    names = []
    for it in items:
        n = it.get("product_name")
        if n:
            names.append(n)
    if not names:
        return "articles"
    short = names[:max_items]
    suffix = "…" if len(names) > max_items else ""
    return ", ".join(short) + (f" {suffix}" if suffix else "")

def _extract_yes_no(text: str) -> Optional[bool]:
    t = (text or "").lower()
    # Non d'abord si "pas neuf"
    if any(x in t for x in ["pas neuf", "non neuf", "utilisé", "utilise", "déjà utilisé", "deja utilise"]):
        return False
    # Oui
    if re.search(r"\b(oui|ouii|ok|d'accord|yes)\b", t):
        return True
    # Non
    if re.search(r"\b(non|nn|nop)\b", t):
        return False
    return None

def extract_exchange_details(text: str) -> Dict[str, Any]:
    """
    Extrait tailles et condition (neuf/non).
    Gère formats:
    - 38/39
    - 38 -> 39, 38→39, 38=>39, 38 a la place de 39
    - j'ai reçu 38 je veux 39
    """
    t = (text or "").lower()

    is_new = _extract_yes_no(t)

    size_received = None
    size_wanted = None
    ambiguous_pair = False

    # Patterns explicites
    m_recv = re.search(r"(reçu|recue|recois|reçois|j'ai\s+reçu|taille\s+reçue|taille\s+recue)\D{0,15}(\d{2})", t)
    if m_recv:
        n = int(m_recv.group(2))
        if 30 <= n <= 50:
            size_received = n

    m_want = re.search(r"(veux|souhaite|voudrais|je\s+veux|taille\s+souhaitée|taille\s+souhaitee)\D{0,15}(\d{2})", t)
    if m_want:
        n = int(m_want.group(2))
        if 30 <= n <= 50:
            size_wanted = n

    # Formats "a/b", "a->b", "a=>b", "a à la place de b"
    m_pair = re.search(r"\b(\d{2})\s*(/|->|→|=>|=+>|a\s+la\s+place\s+de)\s*(\d{2})\b", t)
    if m_pair:
        a = int(m_pair.group(1))
        b = int(m_pair.group(3))
        if 30 <= a <= 50 and 30 <= b <= 50:
            # On considère ça ambigu: on demandera confirmation (option b)
            ambiguous_pair = True
            if size_received is None:
                size_received = a
            if size_wanted is None:
                size_wanted = b

    # Si toujours pas, mais 2 nombres présents
    nums = [int(x) for x in re.findall(r"\b(\d{2})\b", t)]
    sizes = [n for n in nums if 30 <= n <= 50]
    if (size_received is None or size_wanted is None) and len(sizes) >= 2:
        # aussi ambigu
        ambiguous_pair = True
        if size_received is None:
            size_received = sizes[0]
        if size_wanted is None:
            size_wanted = sizes[1]

    return {
        "size_received": size_received,
        "size_wanted": size_wanted,
        "is_new": is_new,
        "ambiguous_pair": ambiguous_pair
    }

def extract_delivery_details(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    # Confirmation d'adresse (si user répond juste oui/non)
    address_confirmed = _extract_yes_no(t)
    problem = None
    if any(k in t for k in ["pas reçu", "pas recu", "pas encore reçu", "pas encore recu"]):
        problem = "not_received"
    elif "retard" in t:
        problem = "delay"
    elif any(k in t for k in ["bloqué", "bloque", "coincé", "coince"]):
        problem = "stuck"
    return {"address_confirmed": address_confirmed, "problem": problem}

def extract_defective_details(text: str) -> Dict[str, Any]:
    t = (text or "").lower()
    # on considère "photo/image" comme intention d'envoyer une preuve
    has_photo = any(k in t for k in ["photo", "image", "vidéo", "video", "capture"])
    # description: on prend le texte brut si contient un mot de défaut
    has_defect_word = _match_any(_DEFECT_INTENT, t)
    defect_description = text.strip() if has_defect_word and len(text.strip()) >= 6 else None
    return {"has_photo": has_photo, "defect_description": defect_description}

def extract_refund_details(text: str) -> Dict[str, Any]:
    confirm_cancel = _extract_yes_no(text)
    return {"confirm_cancel": confirm_cancel}

def build_sav_summary(category: SavCategory, extracted: Dict[str, Any], order_id: str) -> str:
    if category == "exchange_return":
        return f"Résumé SAV: échange | order={order_id} | reçu={extracted.get('size_received')} | voulu={extracted.get('size_wanted')} | neuf={extracted.get('is_new')}"
    if category == "delivery_issue":
        return (
            f"Résumé SAV: livraison | order={order_id} | problème={extracted.get('problem')} "
            f"| adresse_confirmée={extracted.get('address_confirmed')} | nouvelle_adresse={extracted.get('new_address')}"
        )
    if category == "defective":
        return f"Résumé SAV: défaut | order={order_id} | photo={extracted.get('has_photo')} | desc={(extracted.get('defect_description') is not None)}"
    return f"Résumé SAV: remboursement/annulation | order={order_id} | confirmation={extracted.get('confirm_cancel')}"

def _find_exchange_details_in_history(history: Optional[List[Dict[str, Any]]], limit_messages: int = 10) -> Dict[str, Any]:
    """
    Parcourt l'historique du plus récent au plus ancien pour retrouver le dernier
    message user contenant une paire de tailles (sr/sw).
    """
    if not history:
        return {"size_received": None, "size_wanted": None}
    
    messages_checked = 0
    for msg in reversed(history):
        if messages_checked >= limit_messages:
            break
        if msg.get("role") == "user":
            text = (msg.get("content") or "").lower()
            ex = extract_exchange_details(text)
            if ex["size_received"] and ex["size_wanted"]:
                return {"size_received": ex["size_received"], "size_wanted": ex["size_wanted"]}
            messages_checked += 1
    
    return {"size_received": None, "size_wanted": None}

def build_sav_reply(
    category: SavCategory,
    last_order: Optional[Dict[str, Any]],
    user_text: str,
    last_bot_text: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
    session_id: Optional[str] = None,
    channel: str = "web"
) -> str:
    if not last_order:
        return (
            "Je peux vous aider pour le SAV. "
            "Pouvez-vous me donner votre numéro de commande (ex: CMD-20260330-001) ?"
        )

    order_id = last_order.get("order_id", "CMD-XXXX")
    items_txt = format_order_items(last_order)

    def _finalize_with_ticket(answer_text: str, summary: str) -> str:
        if "je transmets" in (answer_text or "").lower() and session_id:
            try:
                from app.core.sav_tickets import create_or_update_ticket

                create_or_update_ticket(
                    session_id=session_id,
                    channel=channel,
                    category=category,
                    order_id=order_id,
                    status="open",
                    summary=summary,
                    last_user_message=(user_text or "").strip(),
                )
            except Exception:
                pass
        return answer_text

    if category == "exchange_return":
        if _is_cancel_exchange_intent(user_text):
            if session_id:
                try:
                    from app.core.sav_tickets import cancel_exchange_ticket
                    cancel_exchange_ticket(session_id=session_id, order_id=order_id, last_user_message=(user_text or "").strip())
                except Exception:
                    pass
            return (
                "D’accord, j’annule votre demande d’échange/retour. Souhaitez-vous autre chose ?\n"
                "1) Suivi livraison\n"
                "2) Changer adresse\n"
                "3) Annuler commande"
            )

        ex = extract_exchange_details(user_text)

        sr, sw, is_new = ex["size_received"], ex["size_wanted"], ex["is_new"]
        ambiguous = ex["ambiguous_pair"]

        # BUG 1 FIX: Si condition donnée (oui/non) mais tailles manquantes dans user_text,
        # chercher dans l'historique
        if is_new is not None and (not sr or not sw):
            hist_sizes = _find_exchange_details_in_history(history)
            if hist_sizes["size_received"] and hist_sizes["size_wanted"]:
                # Retrouvé dans l'historique!
                sr = hist_sizes["size_received"]
                sw = hist_sizes["size_wanted"]
                # Maintenant on a les tailles, on peut finaliser

        # Si tailles détectées mais ambiguës (ex: 38/39) => confirmation
        if ambiguous and sr and sw and not _match_any(_EXCHANGE_INTENT, user_text):
            return (
                f"Je crois comprendre : **taille reçue {sr}** et **taille souhaitée {sw}** pour la commande **{order_id}** ({items_txt}).\n"
                "Confirmez-vous ? (Oui/Non)"
            )

        # Si tailles OK mais condition manquante
        if sr and sw and is_new is None:
            return (
                f"Parfait, j’ai noté : **taille reçue {sr}** → **taille souhaitée {sw}** "
                f"pour la commande **{order_id}** ({items_txt}).\n\n"
                "L’article est-il **neuf / non utilisé** ? (Oui/Non)"
            )

        # Si condition donnée mais tailles toujours manquantes (même après recherche historique)
        if is_new is not None and (not sr or not sw):
            return (
                f"D’accord. Pour la commande **{order_id}** ({items_txt}), indiquez s’il vous plaît :\n"
                "1) la **taille reçue**\n"
                "2) la **taille souhaitée**\n"
                f"(Article {'neuf' if is_new else 'utilisé'} noté.)"
            )

        # Tout complet (inclus si retrouvé dans l'historique)
        if sr and sw and is_new is not None:
            cond = "neuf / non utilisé" if is_new else "utilisé"
            # Mettre à jour ex avec les bonnes valeurs si retrouvées dans historique
            ex["size_received"] = sr
            ex["size_wanted"] = sw
            return _finalize_with_ticket((
                f"Merci. J’ai noté : taille reçue **{sr}**, taille souhaitée **{sw}**, article **{cond}**.\n"
                f"Je transmets votre demande au SAV pour la commande **{order_id}**.\n\n"
                "Votre demande est bien prise en compte."
            ), f"Demande échange/retour: reçu={sr}, souhaité={sw}, état={cond}")

        # Demande initiale (pas assez d'infos)
        return (
            f"D’accord. J’ai retrouvé votre dernière commande **{order_id}** ({items_txt}).\n\n"
            "Pour l’échange/retour, indiquez :\n"
            "1) la **taille reçue**\n"
            "2) la **taille souhaitée**\n"
            "3) l’article est **neuf / non utilisé** ? (Oui/Non)"
        )

    if category == "delivery_issue":
        ex = extract_delivery_details(user_text)
        address_confirmed = ex["address_confirmed"]
        user_text_clean = (user_text or "").strip()
        last_bot_lower = (last_bot_text or "").lower()
        user_lower = (user_text or "").lower()

        wants_address_change = (
            ("changer" in user_lower or "modifier" in user_lower) and "adresse" in user_lower
        )
        tracking_or_delay_markers = [
            "suivre", "suivi", "tracking", "où est", "ou est", "colis", "commande",
            "pas encore arrivée", "pas encore arrivee", "pas encore reçu", "pas encore recu",
            "retard", "en retard"
        ]
        is_tracking_or_delay_request = any(m in user_lower for m in tracking_or_delay_markers)

        # Flow changement d'adresse uniquement
        if address_confirmed is not None and ("adresse" in (last_bot_text or "").lower()):
            if address_confirmed:
                return _finalize_with_ticket((
                    f"Merci pour la confirmation. Je transmets au service livraison pour la commande **{order_id}** ({items_txt})."
                ), "Demande livraison: confirmation adresse")
            return (
                f"D’accord. Pouvez-vous me donner la **nouvelle adresse complète** pour la commande **{order_id}** ?"
            )

        # Si le bot vient de demander la nouvelle adresse et que le user donne une adresse texte
        asked_new_address = (
            "nouvelle adresse" in last_bot_lower or
            "donner la nouvelle adresse" in last_bot_lower
        )
        if asked_new_address and address_confirmed is None and user_text_clean:
            ex["new_address"] = user_text_clean
            return _finalize_with_ticket((
                f"Merci, j’ai noté la nouvelle adresse **{user_text_clean}**. "
                f"Je transmets au service livraison pour la commande **{order_id}** ({items_txt})."
            ), f"Demande livraison: changement adresse ({user_text_clean})")

        # Cas suivi / tracking / retard -> ne pas poser la question d'adresse par défaut
        if is_tracking_or_delay_request and not wants_address_change:
            problem = ex.get("problem") or "tracking_delay"
            ex["problem"] = problem
            return (
                f"D’accord, je vous aide pour le suivi de la commande **{order_id}** ({items_txt}).\n"
                "Pouvez-vous confirmer le **numéro de commande** et me dire si le suivi indique un statut particulier (ex: en transit, bloqué, en retard) ?\n\n"
                "Je peux ensuite vous orienter rapidement."
            )

        # Cas changement d'adresse explicite
        if wants_address_change:
            return (
                f"Je suis désolé pour ce souci. J’ai retrouvé votre dernière commande **{order_id}** ({items_txt}).\n\n"
                "Pouvez-vous confirmer : votre **adresse de livraison** est toujours la même ? (Oui/Non)"
            )

        # Initial delivery_issue générique: orienté suivi, pas adresse
        return (
            f"Je suis désolé pour ce souci de livraison. J’ai retrouvé votre dernière commande **{order_id}** ({items_txt}).\n\n"
            "Pouvez-vous préciser si c’est un **suivi**, un **retard**, ou une **commande non reçue** ?"
        )

    if category == "defective":
        ex = extract_defective_details(user_text)

        # Si description déjà fournie, demander photo si pas mentionnée
        if ex["defect_description"] and not ex["has_photo"]:
            return (
                f"Merci pour les détails. Pour la commande **{order_id}** ({items_txt}), pouvez-vous envoyer une **photo** si possible ?"
            )

        # Si photo mentionnée ou défaut déjà mentionné -> transmettre
        if ex["defect_description"] or ex["has_photo"]:
            return _finalize_with_ticket((
                f"Merci. Je transmets au SAV pour la commande **{order_id}** ({items_txt})."
            ), "Demande défaut produit")

        # Initial
        return (
            f"Je suis désolé pour ça. J’ai retrouvé votre commande **{order_id}** ({items_txt}).\n\n"
            "Pouvez-vous décrire le **défaut** et envoyer une **photo** si possible ?"
        )

    # refund_cancel
    ex = extract_refund_details(user_text)
    confirm = ex["confirm_cancel"]

    # Si réponse oui/non (souvent après question de confirmation)
    if confirm is not None and any(k in (last_bot_text or "").lower() for k in ["confirmez", "annulation", "annuler", "remboursement"]):
        if confirm:
            return _finalize_with_ticket((
                f"Merci. Je transmets votre demande d’annulation/remboursement au SAV pour la commande **{order_id}** ({items_txt})."
            ), "Demande annulation/remboursement")
        return "D’accord. Que souhaitez-vous modifier exactement (adresse / article / quantité) ?"

    # Initial
    return (
        f"D’accord. J’ai retrouvé votre dernière commande **{order_id}** ({items_txt}).\n\n"
        "Confirmez-vous l’**annulation complète** de la commande ? (Oui/Non)"
    )