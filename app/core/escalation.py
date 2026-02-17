# app/core/escalation.py

"""
ESCALATION ENGINE — SESSION BLOCKING VERSION

Déclenche escalation si :
1️⃣ User demande humain
2️⃣ User frustré
3️⃣ Confidence faible
4️⃣ LLM incertain
5️⃣ Trop d’échecs consécutifs

+ ⭐ Bloque la session après escalation
"""

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
# USER FRUSTRATION SIGNALS
# =====================================================
FRUSTRATION_KEYWORDS = [
    "tu ne comprends pas",
    "c'est faux",
    "nul",
    "mauvaise réponse",
    "ça marche pas",
    "encore faux",
    "stupid",
    "useless",
    "worst",
    "not helping"
]

# =====================================================
# USER WANTS HUMAN
# =====================================================
HUMAN_REQUEST_KEYWORDS = [
    "agent humain",
    "humain",
    "parler à quelqu",
    "service client",
    "réclamation",
    "human agent",
    "real person",
    "customer support",
    "help me"
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
# DETECTIONS
# =====================================================
def detect_frustration(user_message: str) -> bool:
    msg = user_message.lower()
    return any(word in msg for word in FRUSTRATION_KEYWORDS)


def detect_human_request(user_message: str) -> bool:
    msg = user_message.lower()
    return any(word in msg for word in HUMAN_REQUEST_KEYWORDS)


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
# ESCALATION DECISION
# =====================================================
def should_escalate(
    user_message: str,
    confidence_score: float,
    llm_answer: str,
    previous_low_conf_count: int = 0
) -> bool:

    if detect_human_request(user_message):
        print("ESCALATION → user wants human")
        return True

    if detect_frustration(user_message):
        print("ESCALATION → frustration detected")
        return True

    if any(p in llm_answer.lower() for p in LOW_CONF_PHRASES):
        print("ESCALATION → AI unsure")
        return True

    if confidence_score <= 0.4:
        print("ESCALATION → low confidence")
        return True

    if previous_low_conf_count >= 2:
        print("ESCALATION → repeated failures")
        return True

    return False
