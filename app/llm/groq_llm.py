# app/llm/groq_llm.py
import os
from groq import Groq
from app.core.memory import get_history

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def generate_response(question, context_chunks, session_id="default"):

    # 🔹 Flatten chunks
    flat_chunks = []
    for item in context_chunks:
        if isinstance(item, list):
            flat_chunks.extend(item)
        else:
            flat_chunks.append(item)

    context_text = "\n".join(flat_chunks)

    # 🔹 Memory
    history = get_history(session_id, last_n=6)
    history_text = ""
    for msg in history:
        history_text += f"{msg['role'].upper()}: {msg['content']}\n"

    # ⭐⭐⭐ NOUVEAU PROMPT CONTRÔLÉ ⭐⭐⭐
    prompt = f"""
You are a STRICT AI assistant for a retail store.

IMPORTANT RULES:
1. You MUST answer using ONLY the KNOWLEDGE BASE below.
2. NEVER invent products.
3. NEVER give general marketing talk.
4. If product not found → say "This product is not available in our store."
5. If user asks for a list → list products exactly as in knowledge.
6. If user asks price/availability → extract from knowledge.

=== CONVERSATION HISTORY ===
{history_text}

=== STORE KNOWLEDGE BASE ===
{context_text}

=== CUSTOMER QUESTION ===
{question}

Provide a direct, factual, short answer based ONLY on store data.
"""

    chat_completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.1-8b-instant",
        temperature=0.2  # 🔥 réduit l'imagination
    )

    return chat_completion.choices[0].message.content
