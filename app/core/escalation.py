# app/core/escalation.py

"""
ESCALATION ENGINE — SESSION BLOCKING VERSION

Déclenche escalation si :
1️⃣ User demande humain
2️⃣ User frustré
3️⃣ Confidence faible
4️⃣ LLM incertain
5️⃣ Trop d'échecs consécutifs

+ ⭐ Bloque la session après escalation
"""

import re

# =====================================================
# 🧠 SESSION ESCALATION STATE (IN MEMORY)
# =====================================================
# session_id -> True/False
ESCALATED_SESSIONS = {}


def activate_escalation(session_id: str):
    """Mark session as escalated (bot stops responding)."""
    ESCALATED_SESSIONS[session_id] = True
    print(f"SESSION {session_id} → ESCALATED")


def is_escalated(session_id: str) -> bool:
    """Check if session already escalated."""
    return ESCALATED_SESSIONS.get(session_id, False)


def reset_escalation(session_id: str):
    """Optional: reset escalation state."""
    if session_id in ESCALATED_SESSIONS:
        del ESCALATED_SESSIONS[session_id]


# =====================================================
# USER FRUSTRATION SIGNALS (🔥 AMÉLIORATION)
# =====================================================
FRUSTRATION_KEYWORDS = [
    # Expressions de mécontentement
    "tu ne comprends pas",
    "tu comprends rien",
    "tu comprends pas",
    "c'est faux",
    "nul",
    "null",  # 🔥 AJOUTÉ
    "nulle",  # 🔥 AJOUTÉ
    "j'en ai marre",
    "marre",
    "mauvais",
    "mauvaise",
    "mauvaise réponse",
    "ça marche pas",
    "ça ne marche pas",
    "encore faux",
    "pas bon",
    "pas correct",
    "incorrect",
    "horrible",
    "catastrophe",
    "pas utile",
    "aucune aide",
    "ne sert à rien",
    "inutile",
    
    # Insultes
    "stupide",
    "débile",
    "idiot",
    "crétin",
    "con",
    "connerie",
    "merde",
    
    # Anglais
    "stupid",
    "useless",
    "worst",
    "bad",
    "terrible",
    "awful",
    "not helping",
    "doesn't work",
    "waste of time"
]

# =====================================================
# USER WANTS HUMAN
# =====================================================
HUMAN_REQUEST_KEYWORDS = [
    "agent humain",
    "humain",
    "parler à quelqu",
    "parler à un",
    "parler avec",
    "service client",
    "réclamation",
    "conseiller",
    "opérateur",
    "support",
    "human agent",
    "real person",
    "customer support",
    "help me",
    "speak to someone"
]

# =====================================================
# LOW CONFIDENCE SIGNALS
# =====================================================
LOW_CONF_PHRASES = [
    "je ne sais pas",
    "pas d'information",
    "je ne trouve pas",
    "désolé",
    "incertain",
    "peut-être"
]

# =====================================================
# PRODUCT NOT FOUND
# =====================================================
NOT_FOUND_PHRASES = [
    "pas disponible",
    "n'est pas disponible",
    "pas dans notre base",
    "aucune information",
    "nous ne trouvons pas",
    "produit non trouvé",
    "introuvable"
]


# =====================================================
# DETECTIONS (🔥 AMÉLIORATION)
# =====================================================
def detect_negative_sentiment(user_message: str) -> bool:
    """
    🔥 NOUVELLE FONCTION : Détecte le sentiment négatif
    """
    msg = user_message.lower()
    
    # Patterns négatifs spécifiques
    negative_patterns = [
        "tu es nul",
        "tu es null",
        "tu es nulle",
        "c'est nul",
        "c'est null",
        "vraiment nul",
        "complètement nul",
        "totalement nul",
        "pas du tout utile",
        "ne m'aide pas"
    ]
    
    return any(pattern in msg for pattern in negative_patterns)


def detect_frustration(user_message: str) -> bool:
    """
    🔥 AMÉLIORÉ : Double vérification (mots-clés + patterns)
    """
    msg = user_message.lower()
    
    # Évite d'interpréter comme frustration des questions produit/information
    if "?" in msg and not any(k in msg for k in ["nul", "marre", "marche pas", "stupide", "con", "merde", "faux", "incorrect"]):
        return False

    # Méthode 1 : Mots-clés directs (mot entier pour les termes courts)
    has_keyword = False
    for word in FRUSTRATION_KEYWORDS:
        w = (word or "").strip().lower()
        if not w:
            continue
        if len(w) <= 4 and " " not in w:
            if re.search(rf"\b{re.escape(w)}\b", msg):
                has_keyword = True
                break
        elif w in msg:
            has_keyword = True
            break
    
    # Méthode 2 : Patterns de sentiment négatif
    has_negative_sentiment = detect_negative_sentiment(user_message)
    
    # 🔥 Escalade si au moins une méthode détecte la frustration
    if has_keyword or has_negative_sentiment:
        print(f"🚨 FRUSTRATION DETECTED: '{user_message}'")
        return True
    
    return False


def detect_human_request(user_message: str) -> bool:
    """
    Détecte si l'utilisateur demande un agent humain
    """
    msg = user_message.lower()
    
    detected = any(word in msg for word in HUMAN_REQUEST_KEYWORDS)
    
    if detected:
        print(f"🚨 HUMAN REQUEST DETECTED: '{user_message}'")
    
    return detected


# =====================================================
# CONFIDENCE SCORE
# =====================================================
def compute_confidence(retrieved_chunks: list, llm_answer: str, intent: str) -> float:

    score = 0.0

    if intent and intent != "other":
        score += 0.25

    if retrieved_chunks:
        score += 0.35

    if llm_answer and len(llm_answer.split()) > 6:
        score += 0.2

    if any(p in llm_answer.lower() for p in LOW_CONF_PHRASES):
        score -= 0.3

    if any(p in llm_answer.lower() for p in NOT_FOUND_PHRASES):
        score -= 0.4

    return round(max(min(score, 1.0), 0.0), 2)


# =====================================================
# ESCALATION DECISION (🔥 AMÉLIORATION)
# =====================================================
def should_escalate(
    user_message: str,
    confidence_score: float,
    llm_answer: str,
    previous_low_conf_count: int = 0
) -> bool:
    """
    🔥 AMÉLIORÉ : Meilleure priorisation des critères
    """
    
    # 1️⃣ PRIORITÉ MAXIMALE : Demande explicite d'agent humain
    if detect_human_request(user_message):
        print("��� ESCALATION → user wants human")
        return True

    # 2️⃣ Frustration utilisateur: escalader seulement si déjà au moins 1 low-conf avant
    if detect_frustration(user_message) and previous_low_conf_count >= 1:
        print("✅ ESCALATION → frustration + prior low confidence")
        return True

    # 3️⃣ IA incertaine
    if any(p in llm_answer.lower() for p in LOW_CONF_PHRASES):
        print("✅ ESCALATION → AI unsure")
        return True

    # 4️⃣ Confiance très basse
    if confidence_score <= 0.4:
        print(f"✅ ESCALATION → low confidence ({confidence_score})")
        return True

    # 5️⃣ Échecs répétés
    if previous_low_conf_count >= 2:
        print(f"✅ ESCALATION → repeated failures ({previous_low_conf_count})")
        return True

    return False