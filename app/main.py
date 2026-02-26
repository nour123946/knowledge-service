# 📦 IMPORTS
from fastapi import FastAPI, UploadFile, File, Security, HTTPException, status, Header
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
from app.core.memory import add_message, get_history
from app.core.escalation import compute_confidence, should_escalate, detect_frustration, LOW_CONF_PHRASES
from app.core.entities import extract_entities
from app.core.feedback import (
    save_feedback, 
    get_feedback_stats, 
    get_negative_feedbacks,
    get_low_confidence_feedbacks
)
import uuid
from app.routers import analytics

# 🔐 LOAD ENV
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "MY_SUPER_ADMIN_TOKEN_123")  # ✅ Changé ici


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
app.include_router(analytics.router)
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


# 📋 INGEST TEST DATA
@app.post("/ingest")
def ingest():
    result = ingest_file("data/business_data.txt")
    return result


# 🔍 VECTOR SEARCH
@app.post("/search")
def search(query: str):
    embedding = embed_texts([query])[0]
    results = search_chunks(embedding, top_k=5)
    return {"results": results}


# 🎯 INTENT DETECTION
@app.post("/intent")
def detect_intent(query: str):
    intent = classify_intent(query)
    return {"intent": intent}


# 💬 GET CONVERSATION HISTORY
@app.get("/history/{session_id}")
def get_conversation_history(session_id: str, last_n: int = 10):
    """Get conversation history for a session"""
    history = get_history(session_id, last_n=last_n)
    return {
        "session_id": session_id,
        "history": history,
        "message_count": len(history)
    }


# 🤖 MAIN AI PIPELINE (AVEC GESTION DES COMMANDES)
class AskRequest(BaseModel):
    query: str
    session_id: str = "default"
    low_conf_history: int = 0
    channel: str = "web"   # web | whatsapp | facebook
    conversation_state: Optional[str] = None  # 🆕 AJOUTÉ


@app.post("/ask")
def ask(request: AskRequest):
    """
    ASSISTANT BUSINESS INTELLIGENT - MULTI-FONCTIONNEL
    """

    query = request.query
    session_id = request.session_id
    channel = request.channel
    low_conf_history = request.low_conf_history
    conversation_state = request.conversation_state

    # 🔥 ÉTAPE 1 : VÉRIFIER L'ESCALATION EN PREMIER (PRIORITÉ ABSOLUE)
    from app.core.escalation import detect_frustration, detect_human_request
    
    immediate_escalation = detect_frustration(query) or detect_human_request(query)
    
    if immediate_escalation:
        logger.warning(f"⚠️ ESCALATION IMMÉDIATE: {query}")
        
        escalation_answer = "Je comprends. Un agent va vous contacter très bientôt."
        
        add_message(session_id, "user", query)
        add_message(session_id, "assistant", escalation_answer)
        
        save_conversation(
            session_id=session_id,
            channel=channel,
            user_message=query,
            ai_response=escalation_answer,
            confidence=0.3,
            escalated=True
        )
        
        return {
            "message_id": str(uuid.uuid4()),
            "answer": escalation_answer,
            "final_answer": escalation_answer,
            "intent": "escalation",
            "confidence": 0.3,
            "confidence_score": 0.3,
            "should_escalate": True,
            "needs_human_agent": True,
            "escalation_reason": _get_escalation_reason(query, 0.3, escalation_answer, low_conf_history),
            "session_id": session_id,
            "conversation_state": "idle",
            "is_order_flow": False,
            "retrieved_chunks": 0
        }
    
    # 🔥 ÉTAPE 2 : CLASSIFIER L'INTENTION
    intent = classify_intent(query)
    
    # 🔥 ÉTAPE 3 : DÉTECTION INTELLIGENTE DU WORKFLOW DE COMMANDE
    
    # 1️⃣ Vérifier si déjà dans un workflow actif
    is_in_order_workflow = (
        conversation_state is not None and 
        conversation_state != "idle" and 
        conversation_state != ""
    )
    
    # 2️⃣ Mots-clés explicites de commande
    explicit_order_keywords = [
        "commander", "acheter", "prendre", "je veux", "je voudrais",
        "donnez-moi", "j'aimerais", "panier", "finaliser", "valider",
        "je prends", "ok je prends", "d'accord", "parfait"
    ]
    
    has_explicit_order_keyword = any(word in query.lower() for word in explicit_order_keywords)
    
    # 3️⃣ Noms de produits disponibles (détection intelligente)
    available_products = [
        "puma", "adidas", "converse", "new balance",
        "ultraboost", "chuck taylor", "rs-x", "574"
    ]
    
    mentions_product = any(product in query.lower() for product in available_products)
    
    # 4️⃣ Mots d'intérêt pour un produit
    interest_keywords = [
        "m'intéresse", "intéressant", "je suis intéressé",
        "ça me plaît", "je veux", "je voudrais"
    ]
    
    shows_interest = any(word in query.lower() for word in interest_keywords)
    
    # 5️⃣ Confirmations courtes (après une proposition)
    short_confirmations = ["oui", "ok", "d'accord", "parfait", "ouais", "yes", "go"]
    is_confirmation = query.lower().strip() in short_confirmations
    
    # 6️⃣ Récupérer le dernier message du bot pour contexte
    history = get_history(session_id, last_n=2)
    last_bot_message = ""
    if history:
        for msg in reversed(history):
            if msg['role'] == 'assistant':
                last_bot_message = msg['content'].lower()
                break
    
    # Le bot a-t-il proposé un produit dans son dernier message ?
    bot_proposed_product = any(product in last_bot_message for product in available_products)
    
    # 🔥 DÉCISION INTELLIGENTE : Lancer le workflow si...
    should_start_workflow = (
        is_in_order_workflow or  # Déjà dans le workflow
        has_explicit_order_keyword or  # Mots explicites
        (mentions_product and shows_interest) or  # Mentionne produit + intérêt
        (is_confirmation and bot_proposed_product) or  # Confirmation après proposition
        (mentions_product and not any(w in query.lower() for w in ["prix", "coût", "combien", "disponible"]))  # Produit mentionné sans question de prix
    )
    
    logger.info(f"""
🔍 WORKFLOW DETECTION:
- is_in_order_workflow: {is_in_order_workflow}
- has_explicit_order_keyword: {has_explicit_order_keyword}
- mentions_product: {mentions_product}
- shows_interest: {shows_interest}
- is_confirmation: {is_confirmation}
- bot_proposed_product: {bot_proposed_product}
→ DECISION: {should_start_workflow}
""")
    
    # 🔥 SI WORKFLOW DÉTECTÉ → LANCER
    if should_start_workflow:
        try:
            from app.workflows.order_workflow import OrderWorkflow
            
            logger.info(f"🛒 LAUNCHING ORDER WORKFLOW: session={session_id}, state={conversation_state}")
            
            workflow = OrderWorkflow(session_id, channel)
            answer, new_state = workflow.handle_user_message(
                user_message=query,
                intent=intent,
                current_state=conversation_state
            )
            
            logger.info(f"✅ ORDER WORKFLOW: new_state={new_state}")
            
            add_message(session_id, "user", query)
            add_message(session_id, "assistant", answer)
            
            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=answer,
                confidence=0.95,
                escalated=False
            )
            
            return {
                "message_id": str(uuid.uuid4()),
                "answer": answer,
                "final_answer": answer,
                "intent": intent,
                "confidence": 0.95,
                "confidence_score": 0.95,
                "should_escalate": False,
                "needs_human_agent": False,
                "session_id": session_id,
                "conversation_state": new_state,
                "is_order_flow": True,
                "retrieved_chunks": 0
            }
        
        except Exception as e:
            logger.error(f"❌ Erreur workflow: {e}", exc_info=True)
            # Continue avec RAG classique
    
    # 🔥 ÉTAPE 4 : FLUX RAG CLASSIQUE
    embedding = embed_texts([query])[0]
    results = search_chunks(embedding, intent=intent, top_k=5)
    entities = extract_entities(query, results)
    answer = generate_response(query, results, session_id=session_id)
    
    add_message(session_id, "user", query)
    add_message(session_id, "assistant", answer)
    
    confidence = compute_confidence(results, answer, intent)
    escalate = should_escalate(query, confidence, answer, low_conf_history)
    
    save_conversation(
        session_id=session_id,
        channel=channel,
        user_message=query,
        ai_response=answer,
        confidence=confidence,
        escalated=escalate
    )
    
    return {
        "message_id": str(uuid.uuid4()),
        "answer": answer,
        "intent": intent,
        "entities": entities,
        "retrieved_knowledge": results,
        "final_answer": answer,
        "confidence": confidence,
        "confidence_score": confidence,
        "should_escalate": escalate,
        "needs_human_agent": escalate,
        "escalation_reason": _get_escalation_reason(query, confidence, answer, low_conf_history) if escalate else None,
        "session_id": session_id,
        "conversation_state": "idle",
        "is_order_flow": False,
        "retrieved_chunks": len(results) if results else 0
    }


# 🔧 HELPER : Escalation reason
def _get_escalation_reason(query: str, confidence: float, answer: str, low_conf_count: int) -> str:
    """
    Determine why escalation is needed
    PRIORITÉ : Frustration > Échecs répétés > Confiance > Incertitude
    """
    from app.core.escalation import detect_frustration, LOW_CONF_PHRASES
    
    # 🔥 1️⃣ PRIORITÉ MAXIMALE : Frustration utilisateur
    if detect_frustration(query):
        return "User frustration detected"
    
    # 2️⃣ Échecs répétés (grave)
    if low_conf_count >= 2:
        return f"Repeated failures: {low_conf_count} times"
    
    # 3️⃣ Confiance très basse (< 0.3)
    if confidence <= 0.3:
        return f"Very low confidence: {confidence}"
    
    # 4️⃣ Confiance basse (< 0.4)
    if confidence <= 0.4:
        return f"Low confidence score: {confidence}"
    
    # 5️⃣ IA incertaine (dernier critère)
    if any(p in answer.lower() for p in LOW_CONF_PHRASES):
        return "AI uncertain about answer"
    
    return "Unknown reason"

# =====================================================
# 🟢 WHATSAPP WEBHOOK AMÉLIORÉ - CB-10
# =====================================================

import logging

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger("knowledge_service")

# ✅ Variables d'environnement Facebook
FACEBOOK_PAGE_TOKEN = os.getenv("FACEBOOK_PAGE_TOKEN")


def send_whatsapp_message(to: str, text: str, use_buttons: bool = False):
    """
    Send message to WhatsApp user
    
    CB-10: Messaging API Integration
    
    Args:
        to: Phone number
        text: Message text
        use_buttons: If True, send with interactive buttons
    """
    
    if not WHATSAPP_TOKEN or not PHONE_NUMBER_ID:
        logger.warning("⚠️ WhatsApp credentials not configured")
        return
    
    url = f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    if use_buttons:
        # Send with interactive buttons
        data = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": {
                "type": "button",
                "body": {"text": text},
                "action": {
                    "buttons": [
                        {
                            "type": "reply",
                            "reply": {"id": "helpful", "title": "👍 Utile"}
                        },
                        {
                            "type": "reply",
                            "reply": {"id": "not_helpful", "title": "👎 Pas utile"}
                        }
                    ]
                }
            }
        }
    else:
        # Simple text message
        data = {
            "messaging_product": "whatsapp",
            "to": to,
            "text": {"body": text}
        }

    try:
        response = requests.post(url, headers=headers, json=data)
        response.raise_for_status()
        logger.info(f"✅ WhatsApp message sent to {to}")
    except Exception as e:
        logger.error(f"❌ Failed to send WhatsApp message: {e}")


@app.get("/webhook/whatsapp")
def whatsapp_verify(hub_mode: str = None, hub_verify_token: str = None, hub_challenge: str = None):
    """WhatsApp webhook verification"""
    logger.info(f"📱 WhatsApp verification request: mode={hub_mode}")
    
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("✅ WhatsApp webhook verified successfully")
        return int(hub_challenge)
    else:
        logger.error("❌ WhatsApp verification failed")
        raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/whatsapp")
async def whatsapp_webhook(payload: dict):
    """
    WhatsApp message handler with media support
    
    CB-10: Messaging API Integration
    
    Handles:
    - Text messages
    - Images (sends description)
    - Audio (sends transcription info)
    - Location (sends address)
    - Interactive buttons responses
    """
    
    logger.info(f"📱 WhatsApp webhook received: {payload}")
    
    # Ignore status updates
    if "entry" not in payload:
        return {"status": "ignored"}

    try:
        # Extract message data
        changes = payload["entry"][0]["changes"][0]["value"]
        
        # Ignore if no messages
        if "messages" not in changes:
            return {"status": "no_messages"}
        
        message = changes["messages"][0]
        user_phone = message["from"]
        message_type = message.get("type", "text")
        
        # Extract text based on message type
        if message_type == "text":
            user_message = message["text"]["body"]
        elif message_type == "button":
            user_message = message["button"]["text"]
        elif message_type == "image":
            user_message = "L'utilisateur a envoyé une image. Que souhaitez-vous savoir ?"
        elif message_type == "audio":
            user_message = "L'utilisateur a envoyé un message vocal. Pouvez-vous reformuler en texte ?"
        elif message_type == "location":
            user_message = "L'utilisateur a partagé sa localisation."
        else:
            user_message = f"Type de message non supporté: {message_type}"
        
        logger.info(f"📩 WhatsApp message from {user_phone}: {user_message}")
        
        # Process through AI pipeline
        ai_response = ask(AskRequest(
            query=user_message,
            session_id=user_phone,
            channel="whatsapp"
        ))

        final_text = ai_response["final_answer"]
        
        # Send response back to WhatsApp
        send_whatsapp_message(user_phone, final_text)
        
        logger.info(f"✅ WhatsApp response sent to {user_phone}")
        
        return {"status": "message_processed"}

    except Exception as e:
        logger.error(f"❌ WhatsApp webhook error: {e}", exc_info=True)
        return {"error": str(e)}


# =====================================================
# 🔵 FACEBOOK MESSENGER WEBHOOK - CB-10
# =====================================================

@app.get("/webhook/facebook")
def facebook_verify(
    hub_mode: str = None,
    hub_verify_token: str = None,
    hub_challenge: str = None
):
    """
    Facebook Messenger webhook verification
    
    CB-10: Messaging API Integration
    """
    
    logger.info(f"📘 Facebook verification request: mode={hub_mode}")
    
    if hub_mode == "subscribe" and hub_verify_token == VERIFY_TOKEN:
        logger.info("✅ Facebook webhook verified successfully")
        return int(hub_challenge)
    else:
        logger.error("❌ Facebook verification failed")
        raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/webhook/facebook")
async def facebook_webhook(payload: dict):
    """
    Facebook Messenger message handler
    
    CB-10: Messaging API Integration
    
    Handles:
    - Text messages
    - Quick replies
    - Postback buttons
    """
    
    logger.info(f"📘 Facebook webhook received: {payload}")
    
    if "entry" not in payload:
        return {"status": "ignored"}

    try:
        for entry in payload["entry"]:
            # Get messaging events
            if "messaging" not in entry:
                continue
            
            for messaging_event in entry["messaging"]:
                sender_id = messaging_event["sender"]["id"]
                
                # Handle text message
                if "message" in messaging_event:
                    message = messaging_event["message"]
                    
                    # Get text
                    if "text" in message:
                        user_message = message["text"]
                    elif "quick_reply" in message:
                        user_message = message["quick_reply"]["payload"]
                    else:
                        continue
                    
                    logger.info(f"📩 Facebook message from {sender_id}: {user_message}")
                    
                    # Process through AI
                    ai_response = ask(AskRequest(
                        query=user_message,
                        session_id=f"fb_{sender_id}",
                        channel="facebook"
                    ))
                    
                    final_text = ai_response["final_answer"]
                    
                    # Send response
                    send_facebook_message(sender_id, final_text)
                
                # Handle postback (button clicks)
                elif "postback" in messaging_event:
                    postback = messaging_event["postback"]
                    payload_text = postback.get("payload", "")
                    
                    logger.info(f"🔘 Facebook postback from {sender_id}: {payload_text}")
                    
                    # Process postback as query
                    ai_response = ask(AskRequest(
                        query=payload_text,
                        session_id=f"fb_{sender_id}",
                        channel="facebook"
                    ))
                    
                    send_facebook_message(sender_id, ai_response["final_answer"])
        
        return {"status": "ok"}
    
    except Exception as e:
        logger.error(f"❌ Facebook webhook error: {e}", exc_info=True)
        return {"error": str(e)}


def send_facebook_message(recipient_id: str, text: str, quick_replies: list = None):
    """
    Send message to Facebook Messenger user
    
    CB-10: Messaging API Integration
    
    Args:
        recipient_id: Facebook user ID
        text: Message text
        quick_replies: Optional list of quick reply buttons
    """
    
    if not FACEBOOK_PAGE_TOKEN:
        logger.warning("⚠️ Facebook page token not configured")
        return
    
    url = "https://graph.facebook.com/v19.0/me/messages"
    headers = {"Content-Type": "application/json"}
    
    params = {"access_token": FACEBOOK_PAGE_TOKEN}
    
    data = {
        "recipient": {"id": recipient_id},
        "message": {"text": text}
    }
    
    # Add quick replies if provided
    if quick_replies:
        data["message"]["quick_replies"] = quick_replies
    
    try:
        response = requests.post(url, headers=headers, params=params, json=data)
        response.raise_for_status()
        logger.info(f"✅ Facebook message sent to {recipient_id}")
    except Exception as e:
        logger.error(f"❌ Failed to send Facebook message: {e}")


# =====================================================
# 🧪 SIMULATION ENDPOINTS - CB-10
# =====================================================

@app.post("/simulate/whatsapp")
def simulate_whatsapp(message: str, phone: str = "33612345678"):
    """
    Simulate WhatsApp message reception (for demo without deployment)
    
    CB-10: Messaging API Integration - Simulation Mode
    
    Perfect for testing the logic without ngrok/deployment
    """
    logger.info(f"📱 SIMULATION - WhatsApp message from {phone}: {message}")
    
    # Process through AI exactly like real webhook
    ai_response = ask(AskRequest(
        query=message,
        session_id=phone,
        channel="whatsapp"
    ))
    
    return {
        "simulation": True,
        "channel": "whatsapp",
        "input": {
            "from": phone,
            "message": message
        },
        "ai_response": {
            "answer": ai_response["final_answer"],
            "confidence": ai_response["confidence"],
            "escalated": ai_response["should_escalate"],
            "intent": ai_response["intent"],
            "entities": ai_response["entities"]
        },
        "what_would_happen_in_production": "This response would be sent back to WhatsApp user via Meta API"
    }


@app.post("/simulate/facebook")
def simulate_facebook(message: str, sender_id: str = "1234567890"):
    """
    Simulate Facebook Messenger message reception
    
    CB-10: Messaging API Integration - Simulation Mode
    """
    logger.info(f"📘 SIMULATION - Facebook message from {sender_id}: {message}")
    
    # Process through AI
    ai_response = ask(AskRequest(
        query=message,
        session_id=f"fb_{sender_id}",
        channel="facebook"
    ))
    
    return {
        "simulation": True,
        "channel": "facebook",
        "input": {
            "sender_id": sender_id,
            "message": message
        },
        "ai_response": {
            "answer": ai_response["final_answer"],
            "confidence": ai_response["confidence"],
            "escalated": ai_response["should_escalate"],
            "intent": ai_response["intent"]
        },
        "what_would_happen_in_production": "This response would be sent back to Facebook Messenger via Graph API"
    }


# =====================================================
# 🔍 WEBHOOK STATUS ENDPOINT - CB-10
# =====================================================

@app.get("/webhook/status")
def webhook_status():
    """
    Check webhook configuration status
    
    CB-10: Messaging API Integration
    """
    return {
        "whatsapp": {
            "webhook_url": "/webhook/whatsapp",
            "verify_url": "/webhook/whatsapp (GET)",
            "verify_token_configured": bool(VERIFY_TOKEN),
            "access_token_configured": bool(WHATSAPP_TOKEN),
            "phone_number_id_configured": bool(PHONE_NUMBER_ID),
            "status": "ready" if (VERIFY_TOKEN and WHATSAPP_TOKEN and PHONE_NUMBER_ID) else "incomplete_configuration"
        },
        "facebook": {
            "webhook_url": "/webhook/facebook",
            "verify_url": "/webhook/facebook (GET)",
            "verify_token_configured": bool(VERIFY_TOKEN),
            "page_token_configured": bool(FACEBOOK_PAGE_TOKEN),
            "status": "ready" if (VERIFY_TOKEN and FACEBOOK_PAGE_TOKEN) else "incomplete_configuration"
        },
        "simulation_endpoints": {
            "whatsapp": "/simulate/whatsapp?message=test&phone=33612345678",
            "facebook": "/simulate/facebook?message=test&sender_id=123456"
        },
        "note": "Use simulation endpoints to test without deployment/ngrok"
    }


# =====================================================
# 📊 ADMIN ENDPOINTS (ALL SECURED)
# =====================================================

# 📊 ADMIN: CONVERSATIONS TABLE (✅ CORRIGÉ)
@app.get("/admin/conversations")
def admin_table_data(
    escalated_only: Optional[bool] = False,
    channel: Optional[str] = None,
    x_api_key: str = Header(None)  # ✅ CHANGÉ ICI
):
    """
    CB-13: Conversation Logs & History
    Get all conversations with filtering options
    """
    # ✅ Vérifier l'API key
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
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
        if convo.get("messages"):
            formatted[session_id]["last_message"] = convo.get("messages", [])[-1].get("message", "")

    # Retourner les conversations sous forme d'une liste organisée
    return list(formatted.values())


# 📊 ADMIN: PERFORMANCE SCORE (SECURED)
@app.get("/admin/performance-score")
def performance_score(api_key: str = Security(verify_api_key)):
    """
    CB-14: Performance Analytics
    Calculate AI performance score
    """
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
    """
    CB-14: Performance Analytics
    Get comprehensive KPIs
    """

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
    """
    CB-13: Conversation Logs & History
    Get recent conversation activity
    """
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
    """
    CB-14: Performance Analytics
    Complete dashboard data
    """

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


# =====================================================
# 🟢 FEEDBACK ENDPOINTS
# =====================================================

class FeedbackRequest(BaseModel):
    message_id: str
    session_id: str
    user_message: str
    bot_response: str
    rating: str  # 'positive' or 'negative'
    comment: Optional[str] = None
    intent: Optional[str] = None
    confidence: Optional[float] = None


@app.post("/feedback")
def submit_feedback(feedback: FeedbackRequest):
    """
    Submit user feedback on bot response
    
    CB-14: Performance Analytics - User Feedback
    """
    
    save_feedback(
        session_id=feedback.session_id,
        message_id=feedback.message_id,
        user_message=feedback.user_message,
        bot_response=feedback.bot_response,
        rating=feedback.rating,
        comment=feedback.comment,
        intent=feedback.intent,
        confidence=feedback.confidence
    )
    
    return {
        "status": "success",
        "message": "Feedback saved successfully",
        "rating": feedback.rating
    }


@app.get("/admin/feedback-stats")
def admin_feedback_stats(api_key: str = Security(verify_api_key)):
    """
    Get feedback statistics
    
    CB-14: Performance Analytics
    """
    
    stats = get_feedback_stats()
    return stats


@app.get("/admin/negative-feedbacks")
def admin_negative_feedbacks(
    limit: int = 50,
    api_key: str = Security(verify_api_key)
):
    """
    Get all negative feedbacks for review
    
    CB-14: Performance Analytics
    """
    
    feedbacks = get_negative_feedbacks(limit=limit)
    return feedbacks


@app.get("/admin/low-confidence-feedbacks")
def admin_low_confidence_feedbacks(
    threshold: float = 0.5,
    limit: int = 50,
    api_key: str = Security(verify_api_key)
):
    """
    Get feedbacks with low confidence scores
    
    CB-14: Performance Analytics
    """
    
    feedbacks = get_low_confidence_feedbacks(threshold=threshold, limit=limit)
    return feedbacks
   # =====================================================
# 📦 ORDERS ENDPOINTS - ADMIN
# =====================================================

@app.get("/admin/orders")
def admin_orders(
    limit: int = 50,
    status: Optional[str] = None,
    x_api_key: str = Header(None)
):
    """
    Get all orders for admin dashboard
    """
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    
    db = get_database()
    orders_collection = db["orders"]
    
    query = {}
    if status:
        query["status"] = status
    
    orders = list(
        orders_collection
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    
    return {
        "total": len(orders),
        "orders": orders
    }


@app.get("/admin/orders/stats")
def admin_orders_stats(x_api_key: str = Header(None)):
    """
    Get orders statistics for dashboard
    """
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    
    db = get_database()
    orders_collection = db["orders"]
    
    total_orders = orders_collection.count_documents({})
    pending_orders = orders_collection.count_documents({"status": "pending"})
    confirmed_orders = orders_collection.count_documents({"status": "confirmed"})
    
    # Calculer le chiffre d'affaires total
    pipeline = [
        {"$group": {
            "_id": None,
            "total_revenue": {"$sum": "$total_price"}
        }}
    ]
    
    revenue_result = list(orders_collection.aggregate(pipeline))
    total_revenue = revenue_result[0]["total_revenue"] if revenue_result else 0
    
    # Calculer le panier moyen
    avg_order_value = total_revenue / total_orders if total_orders > 0 else 0
    
    return {
        "total_orders": total_orders,
        "pending_orders": pending_orders,
        "confirmed_orders": confirmed_orders,
        "total_revenue": round(total_revenue, 2),
        "average_order_value": round(avg_order_value, 2)
    }