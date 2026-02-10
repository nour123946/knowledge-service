# app/main.py
from fastapi import FastAPI, UploadFile, File
import shutil
import os
from dotenv import load_dotenv

# -------------------------
# Load ENV variables (FIRST)
# -------------------------
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

# -------------------------
# Internal modules
# -------------------------
from app.ingestion.pipeline import ingest_file
from app.embeddings.hf_provider import embed_texts
from app.vectorstore.chroma_store import search_chunks
from app.models.intent_classifier import classify_intent
from app.llm.groq_llm import generate_response

from app.core.memory import add_message
from app.core.escalation import compute_confidence, should_escalate
from app.core.entities import extract_entities

# -------------------------
# App Init
# -------------------------
app = FastAPI(title="Knowledge Service")

UPLOAD_DIR = "uploaded_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)

# =====================================================
# 🟢 HEALTH CHECK
# =====================================================
@app.get("/")
def health():
    return {"status": "Knowledge Service Running 🚀"}


# =====================================================
# 🟢 CB-2 : DOCUMENT UPLOAD + INDEXING
# =====================================================
@app.post("/upload")
async def upload_document(file: UploadFile = File(...)):
    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    result = ingest_file(file_path)

    return {
        "filename": file.filename,
        "status": "uploaded and indexed",
        "chunks_indexed": result["chunks_indexed"]
    }


# =====================================================
# 🟢 CB-2 : STATIC INGEST (TEST)
# =====================================================
@app.post("/ingest")
def ingest():
    result = ingest_file("data/business_data.txt")
    return result


# =====================================================
# 🟢 CB-2 : VECTOR SEARCH (RAG Retrieval)
# =====================================================
@app.post("/search")
def search(query: str):
    embedding = embed_texts([query])[0]
    results = search_chunks(embedding, top_k=5)
    return {"results": results}


# =====================================================
# 🟢 CB-3 : INTENT CLASSIFICATION
# =====================================================
@app.post("/intent")
def detect_intent(query: str):
    intent = classify_intent(query)
    return {"intent": intent}


# =====================================================
# 🟢 CB-7 : MEMORY + CB-2/CB-3 + LLM
# =====================================================



@app.post("/ask")
def ask(query: str, session_id: str = "default", low_conf_history: int = 0):

    # 1️⃣ Intent detection
    intent = classify_intent(query)

    # 2️⃣ Retrieve knowledge (RAG)
    embedding = embed_texts([query])[0]
    results = search_chunks(embedding, top_k=5)

    # 🆕 3️⃣ ENTITY EXTRACTION (SMART)
    entities = extract_entities(query, results)

    # 4️⃣ Generate answer using memory
    answer = generate_response(query, results, session_id=session_id)

    # 5️⃣ Save memory
    add_message(session_id, "user", query)
    add_message(session_id, "assistant", answer)

    # 6️⃣ Confidence score
    confidence = compute_confidence(results, answer, intent)

    # 7️⃣ Escalation decision
    escalate = should_escalate(query, confidence, answer, low_conf_history)

    return {
        "intent": intent,
        "entities": entities,             
        "retrieved_knowledge": results,
        "final_answer": answer,
        "confidence_score": confidence,
        "needs_human_agent": escalate
    }



    
import requests

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")

@app.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: dict):

    try:
        # 1️⃣ Extraire message utilisateur
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        user_message = message["text"]["body"]
        user_phone = message["from"]

        # 2️⃣ Appeler ton IA (endpoint /ask)
        ai_response = ask(query=user_message, session_id=user_phone)

        final_text = ai_response["final_answer"]

        # 3️⃣ Envoyer réponse à WhatsApp
        url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"

        headers = {
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        }

        data = {
            "messaging_product": "whatsapp",
            "to": user_phone,
            "text": {"body": final_text}
        }

        requests.post(url, headers=headers, json=data)

        return {"status": "message processed"}

    except Exception as e:
        return {"error": str(e)}
