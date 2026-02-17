# 📦 IMPORTS
from fastapi import FastAPI, UploadFile, File, Security, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import shutil
import os
import requests
from dotenv import load_dotenv
from datetime import datetime, timedelta

from app.core.conversation_logger import save_conversation
from app.core.database import conversation_collection

from app.ingestion.pipeline import ingest_file
from app.embeddings.hf_provider import embed_texts
from app.vectorstore.chroma_store import search_chunks
from app.models.intent_classifier import classify_intent
from app.llm.groq_llm import generate_response
from app.core.memory import add_message
from app.core.escalation import compute_confidence, should_escalate
from app.core.entities import extract_entities


# 🔐 LOAD ENV
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "supersecret")


# 🔐 API KEY SECURITY
api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key"
        )
    return api_key


# 🚀 FASTAPI INIT
app = FastAPI(title="Knowledge Service AI")

# Serve widget folder (index.html + admin_dashboard.html)
app.mount("/widget", StaticFiles(directory="widget"), name="widget")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],     # ok pour demo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

UPLOAD_DIR = "uploaded_docs"
os.makedirs(UPLOAD_DIR, exist_ok=True)


# 🟢 HEALTH CHECK
@app.get("/")
def health():
    return {"status": "Knowledge Service Running 🚀"}


# 📄 DOCUMENT UPLOAD (SECURED)
@app.post("/upload")
async def upload_document(
    file: UploadFile = File(...),
    api_key: str = Security(verify_api_key)
):
    file_path = os.path.join(UPLOAD_DIR, file.filename)

    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)

    result = ingest_file(file_path)

    return {
        "filename": file.filename,
        "status": "uploaded and indexed",
        "chunks_indexed": result.get("chunks_indexed", 0)
    }


# 🤖 MAIN AI PIPELINE
class AskRequest(BaseModel):
    query: str
    session_id: str = "default"
    low_conf_history: int = 0
    channel: str = "web"   # web | whatsapp | facebook


@app.post("/ask")
def ask(request: AskRequest):

    query = request.query
    session_id = request.session_id
    channel = request.channel
    low_conf_history = request.low_conf_history

    # 1) Intent
    intent = classify_intent(query)

    # 2) Retrieval
    embedding = embed_texts([query])[0]
    results = search_chunks(embedding, top_k=5)

    # 3) Entities
    entities = extract_entities(query, results)

    # 4) LLM
    answer = generate_response(query, results, session_id=session_id)

    # 5) Memory
    add_message(session_id, "user", query)
    add_message(session_id, "assistant", answer)

    # 6) Confidence
    confidence = compute_confidence(results, answer, intent)

    # 7) Escalation
    escalate = should_escalate(query, confidence, answer, low_conf_history)

    # 8) Save conversation
    save_conversation(
        session_id=session_id,
        channel=channel,
        user_message=query,
        ai_response=answer,
        confidence=confidence,
        escalated=escalate
    )

    return {
        "intent": intent,
        "entities": entities,
        "retrieved_knowledge": results,
        "final_answer": answer,
        "confidence_score": confidence,
        "needs_human_agent": escalate
    }


# ✅ WHATSAPP WEBHOOK (MESSAGE HANDLER)
@app.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: dict):

    if "entry" not in payload:
        return {"status": "ignored"}

    try:
        message = payload["entry"][0]["changes"][0]["value"]["messages"][0]
        user_message = message["text"]["body"]
        user_phone = message["from"]

        ai_response = ask(AskRequest(
            query=user_message,
            session_id=user_phone,
            channel="whatsapp"
        ))

        final_text = ai_response["final_answer"]

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


# 📊 ADMIN: TABLE (SECURED)
@app.get("/admin/conversations")
def admin_table_data(
    escalated_only: Optional[bool] = False,
    channel: Optional[str] = None,
    api_key: str = Security(verify_api_key)
):
    query: Dict[str, Any] = {}
    if escalated_only:
        query["escalated"] = True
    if channel:
        query["channel"] = channel

    # Récupérer toutes les conversations triées par session (utilisateur)
    conversations = list(
        conversation_collection
        .find(query, {"_id": 0})  # Exclure _id pour une meilleure lisibilité
        .sort("updated_at", -1)
    )

    # Organiser les conversations par session
    formatted = {}
    for convo in conversations:
        session_id = convo.get("session_id")
        if session_id not in formatted:
            formatted[session_id] = {
                "session_id": session_id,
                "channel": convo.get("channel", "web"),
                "escalated": convo.get("escalated", False),
                "message_count": 0,
                "last_message": "",
                "messages": [],
                "updated_at": convo.get("updated_at"),
                "created_at": convo.get("created_at"),
            }

        # Ajouter les messages à la session correspondante
        formatted[session_id]["message_count"] += len(convo.get("messages", []))
        formatted[session_id]["messages"].extend(convo.get("messages", []))
        formatted[session_id]["last_message"] = convo.get("messages", [])[-1].get("message", "")

    # Retourner les conversations sous forme d'une liste organisée
    return list(formatted.values())




# 📊 ADMIN: PERFORMANCE SCORE (SECURED)
@app.get("/admin/performance-score")
def performance_score(api_key: str = Security(verify_api_key)):
    total = conversation_collection.count_documents({})
    escalated = conversation_collection.count_documents({"escalated": True})

    # Calcul de la performance en fonction des escalades et des conversations
    pipeline = [
        {"$unwind": "$messages"},
        {"$match": {"messages.role": "assistant"}},
        {"$group": {"_id": None, "avg_conf": {"$avg": "$messages.confidence"}}}
    ]
    res = list(conversation_collection.aggregate(pipeline))
    avg_conf = float(res[0]["avg_conf"]) if res else 0.0

    escalation_ratio = (escalated / total) if total else 0.0

    # Calcul du score : 70% confiance et 30% des non-escalades
    score = (avg_conf * 70.0) + ((1.0 - escalation_ratio) * 30.0)

    return {
        "performance_score": round(score, 2),
        "average_confidence": round(avg_conf, 2),
        "escalation_ratio": round(escalation_ratio, 2),
        "total_conversations": total,
        "escalated_conversations": escalated
    }



# 📊 ADMIN: KPIs ENRICHIS (SECURED)
@app.get("/admin/kpis")
def admin_kpis(api_key: str = Security(verify_api_key)):

    total = conversation_collection.count_documents({})
    escalated = conversation_collection.count_documents({"escalated": True})

    # channels
    channels = {
        "web": conversation_collection.count_documents({"channel": "web"}),
        "whatsapp": conversation_collection.count_documents({"channel": "whatsapp"}),
        "facebook": conversation_collection.count_documents({"channel": "facebook"}),
    }

    escalation_rate = round((escalated / total) * 100, 2) if total > 0 else 0

    # avg confidence + low conf count
    pipeline_avg = [
        {"$unwind": "$messages"},
        {"$match": {"messages.role": "assistant"}},
        {"$group": {"_id": None, "avg_conf": {"$avg": "$messages.confidence"}}}
    ]
    res_avg = list(conversation_collection.aggregate(pipeline_avg))
    avg_confidence = round(float(res_avg[0]["avg_conf"]), 2) if res_avg else 0

    pipeline_low = [
        {"$unwind": "$messages"},
        {"$match": {"messages.role": "assistant", "messages.confidence": {"$lt": 0.5}}},
        {"$count": "low_conf_count"}
    ]
    res_low = list(conversation_collection.aggregate(pipeline_low))
    low_conf_count = int(res_low[0]["low_conf_count"]) if res_low else 0

    return {
        "total_conversations": total,
        "escalated_conversations": escalated,
        "escalation_rate": escalation_rate,
        "average_confidence": avg_confidence,
        "low_confidence_responses": low_conf_count,
        "channels": channels
    }


# 📊 ADMIN: RECENT ACTIVITY (SECURED)
@app.get("/admin/recent-activity")
def recent_activity(
    limit: int = 20,
    api_key: str = Security(verify_api_key)
):
    conversations = list(
        conversation_collection
        .find({}, {"_id": 0, "session_id": 1, "channel": 1, "escalated": 1, "updated_at": 1, "messages": 1})
        .sort("updated_at", -1)
        .limit(int(limit))
    )

    formatted = []
    for convo in conversations:
        msgs = convo.get("messages", [])
        last_msg = msgs[-1].get("message", "") if msgs else ""
        formatted.append({
            "session_id": convo.get("session_id"),
            "channel": convo.get("channel", "web"),
            "escalated": convo.get("escalated", False),
            "updated_at": convo.get("updated_at"),
            "last_message": last_msg
        })
    return formatted


# 📊 ANALYTICS DASHBOARD (SECURED)
@app.get("/analytics/dashboard")
def dashboard_data(api_key: str = Security(verify_api_key)):

    total = conversation_collection.count_documents({})
    escalated = conversation_collection.count_documents({"escalated": True})
    escalation_rate = round((escalated / total) * 100, 2) if total > 0 else 0

    channels = {
        "web": conversation_collection.count_documents({"channel": "web"}),
        "whatsapp": conversation_collection.count_documents({"channel": "whatsapp"}),
        "facebook": conversation_collection.count_documents({"channel": "facebook"})
    }

    # avg confidence
    pipeline = [
        {"$unwind": "$messages"},
        {"$match": {"messages.role": "assistant"}},
        {"$group": {"_id": None, "avg_confidence": {"$avg": "$messages.confidence"}}}
    ]
    result = list(conversation_collection.aggregate(pipeline))
    avg_confidence = round(float(result[0]["avg_confidence"]), 2) if result else 0

    return {
        "total_conversations": total,
        "escalated_conversations": escalated,
        "escalation_rate": escalation_rate,
        "average_confidence": avg_confidence,
        "channels": channels
    }
