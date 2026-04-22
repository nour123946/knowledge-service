# app/llm/groq_llm.py
import os
import json
import re
from groq import Groq
from app.core.memory import get_history
import logging

logger = logging.getLogger(__name__)

client = Groq(api_key=os.getenv("GROQ_API_KEY"))


def _extract_json_object(text: str) -> dict:
    if not text:
        raise ValueError("Empty Groq response")

    candidate = text.strip()
    if candidate.startswith("```"):
        candidate = re.sub(r"^```(?:json)?\s*", "", candidate, flags=re.I)
        candidate = re.sub(r"\s*```$", "", candidate)

    match = re.search(r"\{.*\}", candidate, flags=re.S)
    if match:
        candidate = match.group(0)

    payload = json.loads(candidate)
    if not isinstance(payload, dict):
        raise ValueError("Router payload is not a JSON object")
    return payload


def route_intent_groq(prompt: str, model: str = "llama-3.3-70b-versatile") -> dict:
    """Call Groq for intent routing and return a strict JSON object."""
    chat_completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0,
        max_tokens=180
    )

    content = chat_completion.choices[0].message.content or ""
    payload = _extract_json_object(content)
    logger.debug(f"🧭 Groq router payload: {payload}")
    return payload


def classify_sav_category_groq(prompt: str, model: str = "llama-3.3-70b-versatile") -> dict:
    """Call Groq for SAV category classification and return a strict JSON object."""
    chat_completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model=model,
        temperature=0,
        max_tokens=150
    )

    content = chat_completion.choices[0].message.content or ""
    payload = _extract_json_object(content)
    logger.debug(f"🧩 Groq SAV category payload: {payload}")
    return payload

def generate_response(question, context_chunks, session_id="default"):
    """
    Generate SHORT and DIRECT response using Groq LLM
    
    CB-4: LLM Response Generation
    CB-7: Conversation Memory
    """

    # 🔹 Flatten chunks
    flat_chunks = []
    for item in context_chunks:
        if isinstance(item, list):
            flat_chunks.extend(item)
        else:
            flat_chunks.append(item)

    context_text = "\n".join(flat_chunks) if flat_chunks else "Pas d'info disponible."

    # 🔹 Memory
    history = get_history(session_id, last_n=6)
    history_text = ""
    for msg in history:
        role = "Client" if msg['role'] == "user" else "Bot"
        history_text += f"{role}: {msg['content']}\n"

    # 🔥 PROMPT COURT ET DIRECT
    prompt = f"""Tu es un assistant commercial EFFICACE et CONCIS.

📌 **RÈGLE ABSOLUE : RÉPONDS EN 2-3 PHRASES MAX !**

📌 **FIABILITÉ (OBLIGATOIRE) :**
- Réponds UNIQUEMENT à partir de la section "BASE DE DONNÉES" ci-dessous.
- Si l'information demandée n'est pas dans la base, dis exactement: "Je n'ai pas cette information dans ma base actuelle." puis pose UNE seule question de clarification.
- N'invente jamais de prix, délai, disponibilité, date ou politique.

📋 **BASE DE DONNÉES :**
{context_text}

✅ **EXEMPLES DE BONNES RÉPONSES :**

Client: "merci"
Bot: "De rien ! 😊"

Client: "bonjour"
Bot: "Bonjour ! Comment puis-je vous aider ?"

Client: "je veux adidas"
Bot: "Parfait ! Les Adidas Ultraboost sont à 420 TND. Vous voulez les commander ?"

Client: "avez vous iphone"
Bot: "Désolé, on n'a pas d'iPhone. On a des chaussures : Puma (310 TND), Adidas (420 TND), Converse (190 TND). Ça vous intéresse ?"

Client: "combien coûte les puma"
Bot: "Les Puma RS-X coûtent 310 TND. 😊"

Client: "je veux passer commande"
Bot: "Super ! Quel produit vous intéresse ?"

❌ **INTERDIT :**
- Écrire plus de 3 phrases
- Raconter des détails inutiles
- Répéter les infos
- Parler de politique de retour sauf si demandé

---

📜 **HISTORIQUE :**
{history_text}

❓ **CLIENT :**
{question}

💬 **TA RÉPONSE (COURTE ET DIRECTE) :**"""

    try:
        chat_completion = client.chat.completions.create(
            messages=[{"role": "user", "content": prompt}],
            model="llama-3.3-70b-versatile",
            temperature=0.5,  # 🔥 Moins créatif = plus concis
            max_tokens=150    # 🔥 LIMITÉ À 150 tokens
        )

        response = chat_completion.choices[0].message.content.strip()
        logger.info(f"✅ Short response generated for session {session_id}")
        return response
    
    except Exception as e:
        logger.error(f"❌ Groq API error: {e}")
        return "Désolé, problème technique. Un conseiller va vous aider. 😊"