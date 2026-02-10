# app/core/escalation.py

# --------------------------
# USER FRUSTRATION SIGNALS
# --------------------------
FRUSTRATION_KEYWORDS = [
    "tu ne comprends pas",
    "c'est faux",
    "nul",
    "mauvaise réponse",
    "agent humain",
    "parler à quelqu'un",
    "service client",
    "réclamation"
]

# --------------------------
# AI LOW CONFIDENCE SIGNALS
# --------------------------
LOW_CONF_PHRASES = [
    "je ne sais pas",
    "pas d'information",
    "je ne trouve pas",
    "désolé",
    "incertain",
    "peut-être"
]

# 🆕 PRODUCT / INFO NOT FOUND SIGNALS
NOT_FOUND_PHRASES = [
    "pas disponible",
    "n'est pas disponible",
    "pas dans notre base",
    "aucune information",
    "nous ne trouvons pas",
    "produit non trouvé",
    "introuvable"
]


# ==========================
# USER FRUSTRATION DETECTION
# ==========================
def detect_frustration(user_message: str) -> bool:
    msg = user_message.lower()
    return any(word in msg for word in FRUSTRATION_KEYWORDS)


# ==========================
# AI CONFIDENCE ESTIMATION
# ==========================
def compute_confidence(retrieved_chunks: list, llm_answer: str, intent: str) -> float:
    """
    Smart AI confidence estimation (0 → 1)
    Works for ANY domain / ANY products
    """

    score = 0.0

    # 1️⃣ Intent understood
    if intent and intent != "other":
        score += 0.25

    # 2️⃣ Knowledge retrieved from vector DB
    if retrieved_chunks:
        score += 0.35

    # 3️⃣ Answer quality (not empty / meaningful)
    if llm_answer and len(llm_answer.split()) > 6:
        score += 0.2

    # 4️⃣ LLM linguistic uncertainty penalty
    if any(p in llm_answer.lower() for p in LOW_CONF_PHRASES):
        score -= 0.3

    # 🆕 5️⃣ Product / info not found penalty (business failure)
    if any(p in llm_answer.lower() for p in NOT_FOUND_PHRASES):
        score -= 0.4

    return round(max(min(score, 1.0), 0.0), 2)


# ==========================
# ESCALATION DECISION ENGINE
# ==========================
def should_escalate(user_message: str, confidence_score: float, llm_answer: str, previous_low_conf_count: int = 0) -> bool:

    # 1️⃣ User frustration
    if detect_frustration(user_message):
        return True

    # 2️⃣ AI says it doesn't know
    if any(p in llm_answer.lower() for p in LOW_CONF_PHRASES):
        return True

    # 3️⃣ Low confidence
    if confidence_score <= 0.4:
        return True

    # 4️⃣ Repeated failures
    if previous_low_conf_count >= 2:
        return True

    return False

