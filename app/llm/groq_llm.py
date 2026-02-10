# app/llm/groq_llm.py
import os
from groq import Groq
from app.core.memory import get_history

client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def generate_response(question, context_chunks, session_id="default"):
    """
    Builds a prompt using:
    - retrieved chunks from vector DB
    - conversation history (memory)
    """

    # ✅ Flatten chunks (because Chroma may return nested lists)
    flat_chunks = []
    for item in context_chunks:
        if isinstance(item, list):
            flat_chunks.extend(item)
        else:
            flat_chunks.append(item)

    context_text = "\n".join(flat_chunks)

    # ✅ Get memory (last turns)
    history = get_history(session_id, last_n=6)
    history_text = ""
    for msg in history:
        history_text += f"{msg['role'].upper()}: {msg['content']}\n"

    prompt = f"""
You are an assistant for a business knowledge system.

Rules:
- Use the KNOWLEDGE BASE first.
- Use CONVERSATION HISTORY to resolve references like "it", "the second one", "that product", etc.
- If the answer is not in the knowledge base, say you don't have that information.

=== CONVERSATION HISTORY ===
{history_text}

=== KNOWLEDGE BASE ===
{context_text}

=== CURRENT QUESTION ===
{question}

Return a precise and professional answer.
"""

    chat_completion = client.chat.completions.create(
        messages=[{"role": "user", "content": prompt}],
        model="llama-3.1-8b-instant"
    )

    return chat_completion.choices[0].message.content
