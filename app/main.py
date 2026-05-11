# 📦 IMPORTS
from fastapi import FastAPI, UploadFile, File, Form, Security, HTTPException, status, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.security import APIKeyHeader
from pydantic import BaseModel
from typing import Optional, List, Dict, Any
import shutil
import os
import requests
import logging
from dotenv import load_dotenv
from datetime import datetime, timedelta
from pathlib import Path
import io
import re

from PIL import Image

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
from app.core.router import route_intent
from app.core.sav_category_router import classify_sav_category
from app.core.sav import detect_sav_category, build_sav_reply
from app.core.customer_ops import (
    build_customer_identifier,
    parse_customer_identifier,
    issue_customer_token,
    validate_customer_token,
    collect_customer_updates,
    format_customer_update,
    get_latest_order_snapshot,
    get_latest_sav_snapshot,
    normalize_sav_status,
    normalize_order_status,
    log_admin_action,
)
from app.utils.stop_intent import is_stop_intent
from app.utils.product_parser import parse_business_data, get_product_by_name, get_available_products
import uuid
from app.routers import analytics

# Configure logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(name)s | %(message)s'
)
logger = logging.getLogger("knowledge_service")

# 🔐 LOAD ENV
load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("VERIFY_TOKEN")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "MY_SUPER_ADMIN_TOKEN_123")

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_BUSINESS_V2_CANDIDATES = [
    BASE_DIR / "data" / "data_businessv2.txt",
    BASE_DIR / "data" / "business_data.txt",
    BASE_DIR / "uploaded_docs" / "business_data.txt",
]
DATA_BUSINESS_V2 = next((path for path in DATA_BUSINESS_V2_CANDIDATES if path.exists()), DATA_BUSINESS_V2_CANDIDATES[0])
DATA_IMAGES_DIR = BASE_DIR / "data" / "images"
SUPPORTED_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png"}


def _parse_products_business_v2(file_path: Path = DATA_BUSINESS_V2) -> List[Dict[str, Any]]:
    _ = file_path
    products = parse_business_data(str(DATA_BUSINESS_V2))
    normalized: List[Dict[str, Any]] = []
    for product in products:
        normalized.append({
            "id": product.get("id"),
            "name": product.get("name"),
            "price": product.get("price_text") or product.get("price"),
            "stock": product.get("stock_status") or ("In stock" if product.get("in_stock") else "Out of stock"),
            "delivery": product.get("delivery_time"),
            "image": product.get("image"),
            "brand": product.get("brand"),
            "tags": product.get("tags"),
        })
    return normalized


def _catalog_products() -> List[Dict[str, Any]]:
    return _parse_products_business_v2(DATA_BUSINESS_V2)


def _catalog_product_names(only_available: bool = False) -> List[str]:
    products = _catalog_products()
    if only_available:
        products = [p for p in products if (p.get("stock") or "").lower().startswith("in stock")]
    return [p["name"] for p in products if p.get("name")]


def _catalog_product_by_name(product_name: str) -> Optional[Dict[str, Any]]:
    return get_product_by_name(product_name, _catalog_products())


def _product_price_value(product: Optional[Dict[str, Any]]) -> float:
    if not product:
        return 0.0
    price = product.get("price")
    if isinstance(price, (int, float)):
        return float(price)
    match = re.search(r"(\d+(?:\.\d+)?)", str(price or ""))
    return float(match.group(1)) if match else 0.0


def _catalog_image_files(products: Optional[List[Dict[str, Any]]] = None) -> List[str]:
    items = products if products is not None else _parse_products_business_v2(DATA_BUSINESS_V2)
    return sorted({(p.get("image") or "").strip() for p in items if (p.get("image") or "").strip()})


def _available_catalog_files() -> List[str]:
    if not DATA_IMAGES_DIR.exists():
        return []
    return sorted(
        p.name
        for p in DATA_IMAGES_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in SUPPORTED_IMAGE_EXTENSIONS
    )


def _normalize_filename(value: str) -> str:
    return (Path(value or "").name or "").strip().lower()


def _exact_catalog_match(uploaded_filename: str, products: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    uploaded_norm = _normalize_filename(uploaded_filename)
    if not uploaded_norm:
        return None

    available_files = {name.lower() for name in _available_catalog_files()}
    if uploaded_norm not in available_files:
        return None

    for product in products:
        if _normalize_filename(product.get("image") or "") == uploaded_norm:
            return product
    return None


def _average_hash_from_bytes(raw: bytes, size: int = 8) -> Optional[List[int]]:
    try:
        image = Image.open(io.BytesIO(raw)).convert("L").resize((size, size))
        pixels = list(image.getdata())
        avg = sum(pixels) / len(pixels)
        return [1 if p >= avg else 0 for p in pixels]
    except Exception:
        return None


def _average_hash_from_path(path: Path, size: int = 8) -> Optional[List[int]]:
    if not path.exists():
        return None
    try:
        image = Image.open(path).convert("L").resize((size, size))
        pixels = list(image.getdata())
        avg = sum(pixels) / len(pixels)
        return [1 if p >= avg else 0 for p in pixels]
    except Exception:
        return None


def _hamming_distance(a: List[int], b: List[int]) -> int:
    return sum(1 for x, y in zip(a, b) if x != y)


def _recognize_product_from_image(raw: bytes) -> Optional[Dict[str, Any]]:
    uploaded_hash = _average_hash_from_bytes(raw)
    if not uploaded_hash:
        return None

    products = _parse_products_business_v2(DATA_BUSINESS_V2)
    if not products:
        return None

    catalog_files = _catalog_image_files(products)
    available_files = _available_catalog_files()
    logger.info("📷 Catalog image filenames from catalog metadata: %s", catalog_files)
    logger.info("📷 Catalog image filenames from disk: %s", available_files)

    best_match: Optional[Dict[str, Any]] = None
    best_distance = 10**9

    for product in products:
        ref_name = (product.get("image") or "").strip()
        if not ref_name:
            continue
        ref_hash = _average_hash_from_path(DATA_IMAGES_DIR / ref_name)
        if not ref_hash:
            continue
        dist = _hamming_distance(uploaded_hash, ref_hash)
        if dist < best_distance:
            best_distance = dist
            best_match = product

    if not best_match:
        return None

    # Threshold tuned for near-identical product image recognition
    if best_distance > 8:
        return None

    return {
        "name": best_match.get("name"),
        "price": best_match.get("price"),
        "stock": best_match.get("stock"),
        "delivery": best_match.get("delivery"),
        "image": best_match.get("image"),
        "distance": best_distance,
    }


# 🔐 API KEY SECURITY
api_key_header = APIKeyHeader(name="x-api-key", auto_error=False)

def verify_api_key(api_key: str = Security(api_key_header)):
    if api_key != ADMIN_API_KEY:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API Key"
        )
    return api_key


def _serialize_datetime_fields(payload: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload or {})
    for key, value in list(out.items()):
        if isinstance(value, datetime):
            out[key] = value.isoformat()
    return out


def _extract_channel_recipient(ticket: Dict[str, Any]) -> str:
    customer_identifier = (ticket.get("customer_identifier") or "").strip()
    if ":" in customer_identifier:
        return customer_identifier.split(":", 1)[1]

    session_id = (ticket.get("session_id") or "").strip()
    if ticket.get("channel") == "facebook" and session_id.startswith("fb_"):
        return session_id[3:]
    return session_id


def _deliver_admin_message(ticket: Dict[str, Any], content: str, admin_user: str) -> Dict[str, Any]:
    from app.core.database import get_database

    db = get_database()
    channel = (ticket.get("channel") or "web").lower()
    recipient = _extract_channel_recipient(ticket)
    now = datetime.utcnow()

    delivery_doc = {
        "ticket_id": ticket.get("ticket_id"),
        "order_id": ticket.get("order_id"),
        "channel": channel,
        "recipient": recipient,
        "content": content,
        "created_by": admin_user,
        "attempts": 0,
        "status": "pending",
        "error": "",
        "created_at": now,
        "updated_at": now,
    }
    insert_res = db["delivery_events"].insert_one(delivery_doc)
    delivery_id = str(insert_res.inserted_id)

    status_value = "failed"
    error_message = ""

    if channel == "web":
        status_value = "sent"
    else:
        for attempt in range(1, 4):
            try:
                if channel == "whatsapp":
                    sent = send_whatsapp_message(recipient, content)
                elif channel == "facebook":
                    sent = send_facebook_message(recipient, content)
                else:
                    raise ValueError(f"Unsupported channel: {channel}")
                if not sent:
                    raise RuntimeError(f"{channel} delivery returned unsuccessful status")
                status_value = "sent"
                error_message = ""
                db["delivery_events"].update_one(
                    {"_id": insert_res.inserted_id},
                    {
                        "$set": {
                            "attempts": attempt,
                            "status": status_value,
                            "error": "",
                            "updated_at": datetime.utcnow(),
                        }
                    }
                )
                break
            except Exception as err:
                status_value = "failed"
                error_message = str(err)
                db["delivery_events"].update_one(
                    {"_id": insert_res.inserted_id},
                    {
                        "$set": {
                            "attempts": attempt,
                            "status": status_value,
                            "error": error_message,
                            "updated_at": datetime.utcnow(),
                        }
                    }
                )

    if channel == "web":
        db["delivery_events"].update_one(
            {"_id": insert_res.inserted_id},
            {
                "$set": {
                    "attempts": 1,
                    "status": "sent",
                    "updated_at": datetime.utcnow(),
                }
            }
        )

    return {
        "delivery_id": delivery_id,
        "status": status_value,
        "channel": channel,
        "attempts": 1 if channel == "web" else db["delivery_events"].find_one({"_id": insert_res.inserted_id}, {"attempts": 1}).get("attempts", 0),
        "error": error_message,
    }


def _append_sav_thread_if_ticket_exists(session_id: str, channel: str, user_text: str, assistant_text: str) -> None:
    try:
        from app.core.sav_tickets import get_latest_active_ticket, add_sav_ticket_message_with_meta

        ticket = get_latest_active_ticket(session_id=session_id, channel=channel)
        if not ticket:
            return

        add_sav_ticket_message_with_meta(ticket["ticket_id"], "user", user_text, author="customer")
        add_sav_ticket_message_with_meta(ticket["ticket_id"], "assistant", assistant_text, author="assistant")
    except Exception:
        logger.debug("Could not append SAV thread entry", exc_info=True)


def _deliver_customer_notification(
    *,
    customer_identifier: str,
    channel: str,
    content: str,
    resource_type: str,
    resource_id: str,
    admin_user: str,
    session_id: str = "",
) -> Dict[str, Any]:
    from app.core.database import get_database

    db = get_database()
    channel_norm = (channel or "web").lower()
    recipient = _extract_channel_recipient({
        "customer_identifier": customer_identifier,
        "session_id": session_id,
        "channel": channel_norm,
    })
    now = datetime.utcnow()

    delivery_doc = {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "customer_identifier": customer_identifier,
        "channel": channel_norm,
        "recipient": recipient,
        "content": content,
        "created_by": admin_user,
        "attempts": 0,
        "status": "pending",
        "error": "",
        "created_at": now,
        "updated_at": now,
    }
    insert_res = db["delivery_events"].insert_one(delivery_doc)

    status_value = "failed"
    error_message = ""
    attempts = 1

    if channel_norm == "web":
        status_value = "sent"
        db["delivery_events"].update_one(
            {"_id": insert_res.inserted_id},
            {
                "$set": {
                    "attempts": attempts,
                    "status": status_value,
                    "error": "",
                    "updated_at": datetime.utcnow(),
                }
            },
        )
    else:
        for attempt in range(1, 4):
            attempts = attempt
            try:
                if channel_norm == "whatsapp":
                    sent = send_whatsapp_message(recipient, content)
                elif channel_norm == "facebook":
                    sent = send_facebook_message(recipient, content)
                else:
                    raise ValueError(f"Unsupported channel: {channel_norm}")

                if not sent:
                    raise RuntimeError(f"{channel_norm} delivery returned unsuccessful status")

                status_value = "sent"
                error_message = ""
                db["delivery_events"].update_one(
                    {"_id": insert_res.inserted_id},
                    {
                        "$set": {
                            "attempts": attempt,
                            "status": status_value,
                            "error": "",
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )
                break
            except Exception as err:
                status_value = "failed"
                error_message = str(err)
                db["delivery_events"].update_one(
                    {"_id": insert_res.inserted_id},
                    {
                        "$set": {
                            "attempts": attempt,
                            "status": status_value,
                            "error": error_message,
                            "updated_at": datetime.utcnow(),
                        }
                    },
                )

    return {
        "delivery_id": str(insert_res.inserted_id),
        "status": status_value,
        "channel": channel_norm,
        "recipient": recipient,
        "attempts": attempts,
        "error": error_message,
    }


def _notify_order_customer(order: Dict[str, Any], admin_user: str, reason: str = "") -> Optional[Dict[str, Any]]:
    if not order:
        return None

    meaningful_statuses = {"confirmed", "shipped", "delivered", "cancelled", "canceled"}

    customer_identifier = order.get("customer_identifier") or build_customer_identifier(order.get("channel", "web"), order.get("session_id", ""))
    latest_item = (order.get("status_history") or [])[-1] if order.get("status_history") else {}
    latest_status = (latest_item.get("status") or order.get("status") or "").strip().lower()
    if latest_status not in meaningful_statuses:
        return None

    update_payload = {
        "kind": "order_status",
        "order_id": order.get("order_id"),
        "status": latest_status,
        "status_label": "",
        "tracking_number": order.get("tracking_number"),
        "timestamp": latest_item.get("changed_at") or order.get("updated_at") or order.get("created_at"),
        "support_message": latest_item.get("note") or reason or "Mise à jour de commande",
    }
    content = format_customer_update(update_payload)
    return _deliver_customer_notification(
        customer_identifier=customer_identifier,
        channel=order.get("channel", "web"),
        content=content,
        resource_type="order",
        resource_id=order.get("order_id", ""),
        admin_user=admin_user,
        session_id=order.get("session_id", ""),
    )


def _notify_sav_customer(ticket: Dict[str, Any], admin_user: str, event_kind: str = "sav_status", admin_message: str = "") -> Optional[Dict[str, Any]]:
    if not ticket:
        return None

    meaningful_statuses = {"open", "in_progress", "resolved", "waiting_customer", "cancelled", "canceled"}

    customer_identifier = ticket.get("customer_identifier") or build_customer_identifier(ticket.get("channel", "web"), ticket.get("session_id", ""))
    latest_item = (ticket.get("status_history") or [])[-1] if ticket.get("status_history") else {}
    latest_status = (latest_item.get("status") or ticket.get("status") or "").strip().lower()
    if event_kind == "sav_status" and latest_status not in meaningful_statuses:
        return None

    update_payload = {
        "kind": event_kind,
        "ticket_id": ticket.get("ticket_id"),
        "status": latest_status,
        "status_label": "",
        "timestamp": latest_item.get("changed_at") or ticket.get("updated_at") or ticket.get("created_at"),
        "support_message": admin_message or latest_item.get("reason") or ticket.get("summary") or "Mise à jour SAV",
    }
    content = format_customer_update(update_payload)
    return _deliver_customer_notification(
        customer_identifier=customer_identifier,
        channel=ticket.get("channel", "web"),
        content=content,
        resource_type="sav_ticket",
        resource_id=ticket.get("ticket_id", ""),
        admin_user=admin_user,
        session_id=ticket.get("session_id", ""),
    )

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


@app.post("/customer/upload-image")
async def customer_upload_image(
    file: UploadFile = File(...),
    session_id: Optional[str] = Form(None),
    channel: Optional[str] = Form("web"),
):
    from app.core.memory import add_message, add_product_candidate, set_current_product

    filename = (file.filename or "").lower()
    ext = Path(filename).suffix
    logger.info("📷 Uploaded image filename: %s", file.filename or "")
    if ext not in SUPPORTED_IMAGE_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Only jpg/png files are allowed")

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty file")

    products = _parse_products_business_v2(DATA_BUSINESS_V2)
    if not products:
        raise HTTPException(status_code=500, detail="Product catalog unavailable")

    catalog_files = _catalog_image_files(products)
    logger.info("📷 Loaded catalog files: %s", catalog_files)
    logger.info("📷 Loaded catalog files from disk: %s", _available_catalog_files())

    exact_match = _exact_catalog_match(file.filename or "", products)
    if exact_match:
        logger.info("📷 Exact filename match found for %s -> %s", file.filename or "", exact_match.get("name"))
        recognition_message = (
            f"📸 Product recognized: {exact_match.get('name')}\n"
            f"💰 Price: {exact_match.get('price')}\n"
            f"📦 Stock: {exact_match.get('stock')}\n"
            f"🚚 Delivery: {exact_match.get('delivery')}"
        )
        add_message(session_id or "", "user", f"📷 Uploaded image: {file.filename or ''}")
        add_message(session_id or "", "assistant", recognition_message)
        set_current_product(session_id or "", exact_match.get("name"))
        add_product_candidate(session_id or "", exact_match.get("name"), source="image_filename", confidence=0.99)
        return {
            "matched": True,
            "product": {
                "name": exact_match.get("name"),
                "price": exact_match.get("price"),
                "stock": exact_match.get("stock"),
                "delivery": exact_match.get("delivery"),
            },
            "message": recognition_message,
            "session_id": session_id or "",
            "channel": channel or "web",
            "uploaded_filename": file.filename or "",
            "match_score": 0,
            "match_source": "filename",
            "current_product": exact_match.get("name"),
        }

    match = _recognize_product_from_image(raw)
    if not match:
        return {
            "matched": False,
            "message": "No matching product found in catalog images.",
            "session_id": session_id or "",
            "channel": channel or "web",
        }

    logger.info(
        "📷 Chosen match score for %s -> %s (distance=%s)",
        file.filename or "",
        match.get("name"),
        match.get("distance"),
    )

    recognition_message = (
        f"📸 Product recognized: {match.get('name')}\n"
        f"💰 Price: {match.get('price')}\n"
        f"📦 Stock: {match.get('stock')}\n"
        f"🚚 Delivery: {match.get('delivery')}"
    )
    add_message(session_id or "", "user", f"📷 Uploaded image: {file.filename or ''}")
    add_message(session_id or "", "assistant", recognition_message)
    set_current_product(session_id or "", match.get("name"))
    add_product_candidate(session_id or "", match.get("name"), source="image_hash", confidence=0.95)

    return {
        "matched": True,
        "product": {
            "name": match.get("name"),
            "price": match.get("price"),
            "stock": match.get("stock"),
            "delivery": match.get("delivery"),
        },
        "message": recognition_message,
        "session_id": session_id or "",
        "channel": channel or "web",
        "uploaded_filename": file.filename or "",
        "match_score": match.get("distance"),
        "match_source": "hash",
        "current_product": match.get("name"),
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


def is_order_status_question(query: str) -> bool:
    q = (query or "").lower()
    sav_exclusions = [
        "sav", "ticket", "échange", "echange", "retour", "remboursement", "réclamation", "reclamation"
    ]
    if any(marker in q for marker in sav_exclusions):
        return False
    markers = [
        "statut", "status", "où en est", "ou en est", "suivre", "suivi", "tracking",
        "expédiée", "expediee", "expédié", "expedie", "livrée", "livree", "annulée", "annulee",
        "pas encore reçu", "pas encore recu", "pas reçu", "pas recu", "pas encore arrivée", "pas encore arrivee",
        "où est ma commande", "ou est ma commande", "où est mon colis", "ou est mon colis"
    ]
    return any(m in q for m in markers)


def is_sav_status_question(query: str) -> bool:
    q = (query or "").lower()
    eta_markers = [
        "délai de livraison", "delai de livraison", "combien de temps la livraison",
        "combien de temps pour la livraison", "dans combien de temps la livraison",
        "eta livraison", "estimated delivery"
    ]
    if any(m in q for m in eta_markers):
        return False

    markers = [
        "mon sav", "ticket sav", "mon ticket", "mon échange", "mon echange", "mon retour",
        "où en est mon sav", "ou en est mon sav", "où en est mon ticket", "ou en est mon ticket",
        "status sav", "statut sav", "statut du sav", "statut du ticket", "statut échange", "statut echange",
        "suivi de mon sav", "suivi sav", "suivi du ticket", "mise à jour sav", "mise a jour sav",
        "dernière réponse sav", "derniere reponse sav", "message du sav", "dernier message",
    ]
    return any(m in q for m in markers)


def format_order_datetime(dt: Any) -> str:
    if dt is None:
        return ""
    if isinstance(dt, datetime):
        return dt.strftime("%d/%m/%Y %H:%M")

    s = str(dt).strip()
    if not s:
        return ""
    try:
        parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return parsed.strftime("%d/%m/%Y %H:%M")
    except Exception:
        clean = s.replace("T", " ")
        if "." in clean:
            clean = clean.split(".", 1)[0]
        return clean


def normalize_order_status_for_user(status: str) -> str:
    status_map = {
        "pending": "En attente de traitement",
        "en attente": "En attente de traitement",
        "confirmed": "Confirmée (en préparation)",
        "confirmée": "Confirmée (en préparation)",
        "shipped": "Expédiée (en cours de livraison)",
        "expédiée": "Expédiée (en cours de livraison)",
        "delivered": "Livrée",
        "livrée": "Livrée",
        "cancelled": "Annulée",
        "annulée": "Annulée",
    }
    return status_map.get((status or "").strip().lower(), status)


def _should_close_sav_flow(answer: str) -> bool:
    t = (answer or "").lower()
    return ("je transmets" in t) or ("j’annule votre demande d’échange/retour" in t) or ("j'annule votre demande d'échange/retour" in t)


def resolve_product_for_order(query: str, session_id: str) -> Dict[str, Any]:
    """
    Resolve which product to add to cart for an implicit order.
    Uses product context memory to disambiguate.
    
    Returns:
        {
            "status": "direct" | "choose" | "ask",
            "product_name": str (if status="direct"),
            "options": List[str] (if status="choose"),
            "confidence": float
        }
    """
    from app.core.memory import get_product_context

    products = _catalog_products()
    available_products = get_available_products(products)
    q_lower = (query or "").lower().strip()
    
    # Rule 1: Query explicitly mentions a product (unique)
    explicit_matches = []
    for product in products:
        product_name = (product.get("name") or "").strip()
        brand = (product.get("brand") or "").strip().lower()
        tags = (product.get("tags") or "").strip().lower()
        name_lower = product_name.lower()
        if not product_name:
            continue

        matched = False
        if name_lower and name_lower in q_lower:
            matched = True
        elif brand and brand in q_lower:
            matched = True
        else:
            for token in [t for t in re.split(r"[\s,;/|]+", tags) if len(t) > 2]:
                if token in q_lower:
                    matched = True
                    break

        if matched and product_name not in [m[1] for m in explicit_matches]:
            explicit_matches.append((name_lower or product_name, product_name))
    
    if len(explicit_matches) == 1:
        logger.info(f"✅ PRODUCT RESOLVE: direct (explicit) -> {explicit_matches[0][1]}")
        return {
            "status": "direct",
            "product_name": explicit_matches[0][1],
            "confidence": 0.95
        }
    
    if len(explicit_matches) > 1:
        # Multiple explicit mentions -> choose
        unique_products = list(dict.fromkeys([m[1] for m in explicit_matches]))
        logger.info(f"✅ PRODUCT RESOLVE: choose (explicit multi) -> {unique_products}")
        return {
            "status": "choose",
            "options": unique_products,
            "confidence": 0.85
        }
    
    # Rule 2: Check product_context
    ctx = get_product_context(session_id)
    candidates = ctx.get("candidates", [])
    selected = ctx.get("current_product") or ctx.get("selected_product")
    
    # If user explicitly selected before (and still fresh)
    if selected:
        logger.info(f"✅ PRODUCT RESOLVE: direct (selected) -> {selected}")
        return {
            "status": "direct",
            "product_name": selected,
            "confidence": 0.92
        }
    
    # Filter high-confidence candidates
    high_conf_candidates = [
        c for c in candidates if c.get("confidence", 0) >= 0.75
    ]
    
    # Rule 3: Exactly 1 high-confidence candidate
    if len(high_conf_candidates) == 1:
        product = high_conf_candidates[0]["name"]
        logger.info(f"✅ PRODUCT RESOLVE: direct (context) -> {product}")
        return {
            "status": "direct",
            "product_name": product,
            "confidence": high_conf_candidates[0].get("confidence", 0.8)
        }
    
    # Rule 4: Multiple candidates -> ask user to choose
    if len(high_conf_candidates) >= 2:
        options = [c["name"] for c in high_conf_candidates]
        logger.info(f"✅ PRODUCT RESOLVE: choose (context multi) -> {options}")
        return {
            "status": "choose",
            "options": options,
            "confidence": 0.75
        }
    
    # Rule 5: No candidates -> ask
    logger.info(f"✅ PRODUCT RESOLVE: ask (no context)")
    options = [p.get("name") for p in available_products if p.get("name")]
    return {
        "status": "ask",
        "options": options,
        "confidence": 0.0
    }


def track_product_mention(query: str, response: str, session_id: str, intent: str) -> None:
    """
    Track products mentioned in user query or bot response.
    Updates product_context for future implicit orders.
    """
    from app.core.memory import add_product_candidate

    products = _catalog_products()
    
    q_lower = (query or "").lower()
    r_lower = (response or "").lower()
    
    # Check if this is a product-related query
    product_markers = ["prix", "coût", "cout", "dispo", "disponible", "stock", "couleur", "taille", "caractéristique"]
    is_product_query = any(m in q_lower for m in product_markers)
    
    # Track products mentioned in query
    for product in products:
        product_name = (product.get("name") or "").strip()
        if not product_name:
            continue
        keywords = [
            product_name.lower(),
            (product.get("brand") or "").strip().lower(),
        ]
        tags = (product.get("tags") or "").strip().lower()
        keywords.extend([token for token in re.split(r"[\s,;/|]+", tags) if len(token) > 2])

        if any(keyword and keyword in q_lower for keyword in keywords):
            confidence = 0.85 if is_product_query else 0.75
            add_product_candidate(session_id, product_name, source="query", confidence=confidence)
    
    # Track products mentioned in response (if it's a product description)
    if is_product_query or intent == "info":
        for product in products:
            product_name = (product.get("name") or "").strip()
            if not product_name:
                continue
            keywords = [
                product_name.lower(),
                (product.get("brand") or "").strip().lower(),
            ]
            tags = (product.get("tags") or "").strip().lower()
            keywords.extend([token for token in re.split(r"[\s,;/|]+", tags) if len(token) > 2])
            if any(keyword and keyword in r_lower for keyword in keywords):
                add_product_candidate(session_id, product_name, source="response", confidence=0.88)

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

    immediate_escalation = detect_human_request(query) or (detect_frustration(query) and low_conf_history >= 1)

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
    q = (query or "").lower()
    state = (conversation_state or "").strip().lower()

    gratitude_only_markers = ["merci", "merci beaucoup", "thanks", "thank you", "ok merci"]
    if q.strip() in gratitude_only_markers:
        answer = "Avec plaisir 😊"
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
            "intent": "thanks",
            "confidence": 0.95,
            "confidence_score": 0.95,
            "should_escalate": False,
            "needs_human_agent": False,
            "escalation_reason": None,
            "session_id": session_id,
            "conversation_state": state if state else "idle",
            "is_order_flow": False,
            "retrieved_chunks": 0
        }

    howto_markers = ["comment", "comment faire", "c'est quoi", "quels", "quelle", "où", "quand", "combien", "?"]
    is_howto = any(m in q for m in howto_markers)

    sav_topics = ["retour", "retourner", "échange", "echanger", "échanger", "remboursement", "rembourser", "annuler"]
    mentions_sav_topic = any(t in q for t in sav_topics)

    action_markers = ["je veux", "je voudrais", "je souhaite", "je demande", "j'aimerais", "aide-moi", "aidez-moi", "lance", "ouvrir", "créer"]
    explicit_sav_action = mentions_sav_topic and any(m in q for m in action_markers)
   
    # 🔥 ÉTAPE 3 : ÉTAT DES FLOWS (verrou)
    # ✅ sécurité: un état terminal ou un choice sur état terminal => idle
    if state == "order_placed":
        state = "idle"
        conversation_state = "idle"

    if state.startswith("choice_order_vs_sav:"):
        prev = state.split(":", 1)[1].strip()
        if prev in {"order_placed", "completed", "done", "idle", ""}:
            state = "idle"
            conversation_state = "idle"

    # ✅ Commande: seulement les états de commande (pas SAV)
    order_terminal_states = {"order_placed", "completed", "done"}
    is_in_order_workflow = (
         state not in ("", "idle") and
         (not state.startswith("sav_")) and
         (not state.startswith("choice_order_vs_sav:")) and
         (state not in order_terminal_states)
    )
    # ✅ SAV flow
    is_in_sav_flow = state.startswith("sav_") or (state == "sav_waiting_category")

    # ✅ Choix 1/2
    is_in_choice_flow = state.startswith("choice_order_vs_sav:")

    # ✅ FAST-PATH statut commande depuis DB (status admin dashboard)
    if is_order_status_question(query) and not is_in_choice_flow:
        from app.core.database import get_database

        db = get_database()
        last_order = db["orders"].find_one({"session_id": session_id}, sort=[("created_at", -1)])

        if last_order:
            order_id = last_order.get("order_id", "N/A")
            status_raw = (last_order.get("status") or "pending").lower()
            status_short_map = {
                "pending": "En attente",
                "confirmed": "Confirmée",
                "shipped": "Expédiée",
                "delivered": "Livrée",
                "cancelled": "Annulée",
            }
            status_short = status_short_map.get(status_raw, status_raw)
            status_label = normalize_order_status_for_user(status_raw)
            created_at = last_order.get("created_at")
            created_at_fmt = format_order_datetime(created_at)
            updated_at_fmt = format_order_datetime(last_order.get("updated_at"))
            items = last_order.get("items") or []
            product = items[0].get("product_name") if items and isinstance(items[0], dict) else None
            tracking_number = last_order.get("tracking_number")

            q_lower = (query or "").lower()
            asked_delivered = any(x in q_lower for x in ["livrée", "livree"])
            asked_shipped = any(x in q_lower for x in ["expédiée", "expediee", "expédié", "expedie"])
            asked_cancelled = any(x in q_lower for x in ["annulée", "annulee"])

            if asked_delivered:
                yn = "Oui" if status_raw == "delivered" else "Non"
                answer = f"{yn}, votre commande **{order_id}** a le statut **{status_short}**."
            elif asked_shipped:
                yn = "Oui" if status_raw == "shipped" else "Non"
                answer = f"{yn}, votre commande **{order_id}** a le statut **{status_short}**."
            elif asked_cancelled:
                yn = "Oui" if status_raw == "cancelled" else "Non"
                answer = f"{yn}, votre commande **{order_id}** a le statut **{status_short}**."
            else:
                details = []
                if product:
                    details.append(f"Produit: **{product}**")
                if created_at_fmt:
                    details.append(f"Date: **{created_at_fmt}**")
                if tracking_number:
                    details.append(f"Tracking: **{tracking_number}**")
                details_text = ("\n" + " | ".join(details)) if details else ""
                answer = f"Le statut de votre commande **{order_id}** est: **{status_label}**.{details_text}"

            status_explanation = {
                "confirmed": "Elle est en préparation. Elle n’est pas encore expédiée.",
                "shipped": "Elle est en route.",
                "pending": "En attente de validation.",
            }.get(status_raw)
            if status_explanation:
                answer += f"\n{status_explanation}"

            if tracking_number:
                answer += f"\nNuméro de tracking : **{tracking_number}**"
            if updated_at_fmt:
                answer += f"\nDernière mise à jour : **{updated_at_fmt}**"
            answer += "\nSouhaitez-vous connaître le délai de livraison estimé ?"

            add_message(session_id, "user", query)
            add_message(session_id, "assistant", answer)
            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=answer,
                confidence=0.93,
                escalated=False
            )

            return {
                "message_id": str(uuid.uuid4()),
                "answer": answer,
                "final_answer": answer,
                "intent": "order_status",
                "route": "sav",
                "confidence": 0.93,
                "confidence_score": 0.93,
                "should_escalate": False,
                "needs_human_agent": False,
                "escalation_reason": None,
                "session_id": session_id,
                "conversation_state": "idle",
                "is_order_flow": False,
                "retrieved_chunks": 0
            }

    # ✅ FAST-PATH statut SAV depuis DB
    if is_sav_status_question(query) and not is_in_choice_flow:
        from app.core.database import get_database

        db = get_database()
        customer_identifier = build_customer_identifier(channel, session_id)
        latest_ticket = db["sav_tickets"].find_one({"customer_identifier": customer_identifier}, sort=[("updated_at", -1)])

        if latest_ticket:
            ticket_id = latest_ticket.get("ticket_id", "N/A")
            status_label = normalize_sav_status(latest_ticket.get("status", "open"))
            last_admin_message = ""
            for msg in reversed(latest_ticket.get("messages_thread") or []):
                if (msg.get("role") or "").lower() in {"admin", "assistant", "bot"}:
                    last_admin_message = msg.get("content") or ""
                    break

            last_updated = format_order_datetime(latest_ticket.get("updated_at"))
            answer = f"Le statut de votre SAV **{ticket_id}** est: **{status_label}**."
            if last_admin_message:
                answer += f"\nDernier message admin : {last_admin_message}"
            if last_updated:
                answer += f"\nMis à jour le {last_updated}"

            add_message(session_id, "user", query)
            add_message(session_id, "assistant", answer)
            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=answer,
                confidence=0.92,
                escalated=False
            )

            return {
                "message_id": str(uuid.uuid4()),
                "answer": answer,
                "final_answer": answer,
                "intent": "sav_status",
                "route": "sav",
                "confidence": 0.92,
                "confidence_score": 0.92,
                "should_escalate": False,
                "needs_human_agent": False,
                "escalation_reason": None,
                "session_id": session_id,
                "conversation_state": "idle",
                "is_order_flow": False,
                "retrieved_chunks": 0
            }
    
    # ✅ BUG 2 FIX: Si on est EN SAV actif (pas waiting_category)
    # Chercher si l'user veut changer de catégorie SAV
    if is_in_sav_flow and (not state == "sav_waiting_category") and len((query or "").strip()) > 4:
        # Éviter de switcher sur de simples confirmations (oui/non)
        q_lower = (query or "").lower().strip()
        is_simple_confirmation = q_lower in {"oui", "non", "ok", "ouais", "yes", "no", "nope", "1", "2", "3", "4", "5"} or q_lower.isdigit()
        
        if not is_simple_confirmation:
            # Chercher si une autre catégorie SAV est explicitement mentionnée
            history_for_switch = get_history(session_id, last_n=4)
            last_bot_msg = ""
            if history_for_switch:
                for msg in reversed(history_for_switch):
                    if msg.get("role") == "assistant":
                        last_bot_msg = (msg.get("content") or "")
                        break
            
            new_sav_cat = detect_sav_category(query, last_bot_msg)
            current_cat = state.replace("sav_", "") if state.startswith("sav_") else None
            
            # Si catégorie détectée et DIFFÉRENTE de la courante => SWITCH
            if new_sav_cat and new_sav_cat != current_cat:
                logger.info(f"🔄 SAV SWITCH: {current_cat} -> {new_sav_cat} (state={state})")
                from app.core.database import get_database
                
                db = get_database()
                last_order = db["orders"].find_one({"session_id": session_id}, sort=[("created_at", -1)])
                
                switch_answer = build_sav_reply(
                    category=new_sav_cat,
                    last_order=last_order,
                    user_text=query,
                    last_bot_text=last_bot_msg,
                    history=history_for_switch,
                    session_id=session_id,
                    channel=channel,
                )
                
                add_message(session_id, "user", query)
                add_message(session_id, "assistant", switch_answer)
                save_conversation(
                    session_id=session_id,
                    channel=channel,
                    user_message=query,
                    ai_response=switch_answer,
                    confidence=0.8,
                    escalated=False
                )
                
                next_state = "idle" if _should_close_sav_flow(switch_answer) else f"sav_{new_sav_cat}"
                return {
                    "message_id": str(uuid.uuid4()),
                    "answer": switch_answer,
                    "final_answer": switch_answer,
                    "intent": "sav_switch",
                    "confidence": 0.8,
                    "confidence_score": 0.8,
                    "should_escalate": False,
                    "needs_human_agent": False,
                    "session_id": session_id,
                    "conversation_state": next_state,
                    "is_order_flow": False,
                    "retrieved_chunks": 0
                }
    # ✅ HANDLER CHOIX 1/2 (reprendre commande ou passer SAV)
    if is_in_choice_flow:
        prev_state = conversation_state.split(":", 1)[1]  # ex: order_collecting_address
        choice = query.strip()

        if choice == "1":
            msg = "D’accord, on continue la commande."
            add_message(session_id, "user", query)
            add_message(session_id, "assistant", msg)
            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=msg,
                confidence=0.9,
                escalated=False
            )
            return {
                "message_id": str(uuid.uuid4()),
                "answer": msg,
                "final_answer": msg,
                "intent": "resume_order",
                "confidence": 0.9,
                "confidence_score": 0.9,
                "should_escalate": False,
                "needs_human_agent": False,
                "session_id": session_id,
                "conversation_state": prev_state,  # on reprend exactement l’ancien state
                "is_order_flow": True,
                "retrieved_chunks": 0
            }

        if choice == "2":
            msg = "D’accord, passons au SAV. Décrivez votre demande (échange, livraison, défaut, remboursement)."
            add_message(session_id, "user", query)
            add_message(session_id, "assistant", msg)
            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=msg,
                confidence=0.9,
                escalated=False
            )
            return {
                "message_id": str(uuid.uuid4()),
                "answer": msg,
                "final_answer": msg,
                "intent": "switch_to_sav",
                "confidence": 0.9,
                "confidence_score": 0.9,
                "should_escalate": False,
                "needs_human_agent": False,
                "session_id": session_id,
                "conversation_state": "sav_waiting_category",
                "is_order_flow": False,
                "retrieved_chunks": 0
            }

        msg = "Répondez par 1 ou 2 s’il vous plaît."
        return {
            "message_id": str(uuid.uuid4()),
            "answer": msg,
            "final_answer": msg,
            "intent": "choice_order_vs_sav",
            "confidence": 0.8,
            "confidence_score": 0.8,
            "should_escalate": False,
            "needs_human_agent": False,
            "session_id": session_id,
            "conversation_state": conversation_state,
            "is_order_flow": True,
            "retrieved_chunks": 0
        } 
    if state == "awaiting_sav_launch_confirmation":
            if q in ["oui", "ok", "d'accord", "yes", "go"]:
                msg = (
                "D’accord. Pour quel type de demande ?\n"
                "1) Retour\n2) Échange\n3) Remboursement\n4) Livraison\n5) Défaut produit\n\n"
                "Répondez par un chiffre."
                )
                add_message(session_id, "user", query)
                add_message(session_id, "assistant", msg)
                save_conversation(session_id=session_id, channel=channel, user_message=query, ai_response=msg, confidence=0.9, escalated=False)
                return {
                "message_id": str(uuid.uuid4()),
                "answer": msg,
                "final_answer": msg,
                "intent": "sav_waiting_category",
                "confidence": 0.9,
                "confidence_score": 0.9,
                "should_escalate": False,
                "needs_human_agent": False,
                "session_id": session_id,
                "conversation_state": "sav_waiting_category",
                "is_order_flow": False,
                "retrieved_chunks": 0
                }

            if q in ["non", "no", "pas maintenant", "plus tard"]:
                msg = "Très bien. Si vous voulez, je peux lancer une demande quand vous le souhaitez."
                add_message(session_id, "user", query)
                add_message(session_id, "assistant", msg)
                save_conversation(session_id=session_id, channel=channel, user_message=query, ai_response=msg, confidence=0.9, escalated=False)
                return {
                "message_id": str(uuid.uuid4()),
                "answer": msg,
                "final_answer": msg,
                "intent": "idle",
                "confidence": 0.9,
                "confidence_score": 0.9,
                "should_escalate": False,
                "needs_human_agent": False,
                "session_id": session_id,
                "conversation_state": "idle",
                "is_order_flow": False,
                "retrieved_chunks": 0
                }

            msg = "Répondez par Oui ou Non s’il vous plaît."
            return {
            "message_id": str(uuid.uuid4()),
            "answer": msg,
            "final_answer": msg,
            "intent": "awaiting_sav_launch_confirmation",
            "confidence": 0.8,
            "confidence_score": 0.8,
            "should_escalate": False,
            "needs_human_agent": False,
            "session_id": session_id,
            "conversation_state": "awaiting_sav_launch_confirmation",
            "is_order_flow": False,
            "retrieved_chunks": 0
            }
    
    # 🔥 ÉTAPE CRITIQUE: ARRÊT/FERMETURE SAV FLOW
    # Si l'utilisateur est dans un state SAV et veut quitter => idle + close message
    if state.startswith("sav_") and is_stop_intent(query):
        answer = "D'accord, je reste disponible si vous avez besoin d'aide à l'avenir. 😊"
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
            "intent": "close_sav_flow",
            "confidence": 0.95,
            "confidence_score": 0.95,
            "should_escalate": False,
            "needs_human_agent": False,
            "session_id": session_id,
            "conversation_state": "idle",
            "is_order_flow": False,
            "retrieved_chunks": 0
        }
    
    if state == "sav_waiting_category":
            choice = (query or "").strip()

            mapping = {
        "1": "exchange_return",  # Retour
        "2": "exchange_return",  # Échange
        "3": "refund_cancel",    # Remboursement
        "4": "delivery_issue",   # Livraison
        "5": "defective"         # Défaut
            }

            cat = mapping.get(choice)
            if not cat:
                msg = "Répondez par 1, 2, 3, 4 ou 5 s’il vous plaît."
                return {
            "message_id": str(uuid.uuid4()),
            "answer": msg,
            "final_answer": msg,
            "intent": "sav_waiting_category",
            "confidence": 0.8,
            "confidence_score": 0.8,
            "should_escalate": False,
            "needs_human_agent": False,
            "session_id": session_id,
            "conversation_state": "sav_waiting_category",
            "is_order_flow": False,
            "retrieved_chunks": 0
                }

            from app.core.database import get_database

            db = get_database()
            last_order = db["orders"].find_one({"session_id": session_id}, sort=[("created_at", -1)])

            history_for_sav = get_history(session_id, last_n=8)
            sav_answer = build_sav_reply(
                category=cat,
                last_order=last_order,
                user_text="" if (query or "").strip().isdigit() else query,
                last_bot_text="",
                history=history_for_sav,
                session_id=session_id,
                channel=channel,
            )

            add_message(session_id, "user", query)
            add_message(session_id, "assistant", sav_answer)
            save_conversation(session_id=session_id, channel=channel, user_message=query, ai_response=sav_answer, confidence=0.85, escalated=False)
            _append_sav_thread_if_ticket_exists(session_id=session_id, channel=channel, user_text=query, assistant_text=sav_answer)
            next_state = "idle" if _should_close_sav_flow(sav_answer) else f"sav_{cat}"
            return {
                "message_id": str(uuid.uuid4()),
                "answer": sav_answer,
                "final_answer": sav_answer,
                "intent": "sav_flow",
                "confidence": 0.85,
                "confidence_score": 0.85,
                "should_escalate": False,
                "needs_human_agent": False,
                "session_id": session_id,
                "conversation_state": next_state,
                "is_order_flow": False,
                "retrieved_chunks": 0
            }
        # ✅ Option 1 : si l'utilisateur demande du SAV pendant une commande active → demander choix
    if is_in_order_workflow and (not is_in_sav_flow) and (not is_in_choice_flow):
        q = (query or "").lower()
        is_complaint_or_explicit_sav = False
        is_complaint_or_explicit_sav = any(w in q for w in [
            "sav", "retour", "retourner", "échange", "echanger", "échanger",
            "remboursement", "rembourser", "annuler",
            "pas reçu", "pas recu", "non reçu", "non recu", "retard", "abîmé", "abime", "cassé", "casse", "défectueux", "defectueux",
            "problème", "probleme", "souci", "réclamation", "reclamation", "plainte"
      ])
        if is_complaint_or_explicit_sav:
            choice_answer = (
                "Vous êtes en train de finaliser une commande.\n\n"
                "Souhaitez-vous :\n"
                "1) Continuer la commande\n"
                "2) Stopper et passer au SAV\n\n"
                "Répondez par 1 ou 2."
            )

            add_message(session_id, "user", query)
            add_message(session_id, "assistant", choice_answer)

            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=choice_answer,
                confidence=0.9,
                escalated=False
            )

            return {
                "message_id": str(uuid.uuid4()),
                "answer": choice_answer,
                "final_answer": choice_answer,
                "intent": "choice_order_vs_sav",
                "confidence": 0.9,
                "confidence_score": 0.9,
                "should_escalate": False,
                "needs_human_agent": False,
                "session_id": session_id,
                "conversation_state": f"choice_order_vs_sav:{conversation_state}",
                "is_order_flow": True,
                "retrieved_chunks": 0
            }

    # 🧭 ROUTER LLM pour les messages hors workflow (idle / empty)
    if (not is_in_order_workflow) and (not is_in_sav_flow) and (not is_in_choice_flow):
        history_router = get_history(session_id, last_n=4)
        route_info = route_intent(query, session_id=session_id, state=state, history=history_router)
        route = (route_info.get("route") or "info").lower()
        route_confidence = float(route_info.get("confidence") or 0.0)
        route_reason = route_info.get("reason") or ""

        logger.debug(
            f"🧭 ROUTER session={session_id} state={state} route={route} "
            f"confidence={route_confidence:.2f} reason={route_reason}"
        )

        if route == "human":
            escalation_answer = "Je comprends. Un agent va vous contacter très bientôt."

            add_message(session_id, "user", query)
            add_message(session_id, "assistant", escalation_answer)

            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=escalation_answer,
                confidence=max(0.3, min(route_confidence or 0.3, 0.5)),
                escalated=True
            )

            return {
                "message_id": str(uuid.uuid4()),
                "answer": escalation_answer,
                "final_answer": escalation_answer,
                "intent": "escalation",
                "route": route,
                "route_confidence": route_confidence,
                "route_reason": route_reason,
                "confidence": max(0.3, min(route_confidence or 0.3, 0.5)),
                "confidence_score": max(0.3, min(route_confidence or 0.3, 0.5)),
                "should_escalate": True,
                "needs_human_agent": True,
                "escalation_reason": route_reason or "Human route requested",
                "session_id": session_id,
                "conversation_state": "idle",
                "is_order_flow": False,
                "retrieved_chunks": 0
            }

        if route == "order":
            try:
                from app.workflows.order_workflow import OrderWorkflow

                logger.info(f"🛒 ROUTER -> ORDER: session={session_id}, state={conversation_state}")
                workflow = OrderWorkflow(session_id, channel)
                answer, new_state = workflow.handle_user_message(
                    user_message=query,
                    intent=intent,
                    current_state=conversation_state
                )
                if (new_state or "").strip().lower() == "order_placed":
                    new_state = "idle"

                add_message(session_id, "user", query)
                add_message(session_id, "assistant", answer)

                save_conversation(
                    session_id=session_id,
                    channel=channel,
                    user_message=query,
                    ai_response=answer,
                    confidence=max(0.8, route_confidence),
                    escalated=False
                )

                return {
                    "message_id": str(uuid.uuid4()),
                    "answer": answer,
                    "final_answer": answer,
                    "intent": intent,
                    "route": route,
                    "route_confidence": route_confidence,
                    "route_reason": route_reason,
                    "confidence": max(0.8, route_confidence),
                    "confidence_score": max(0.8, route_confidence),
                    "should_escalate": False,
                    "needs_human_agent": False,
                    "escalation_reason": None,
                    "session_id": session_id,
                    "conversation_state": new_state,
                    "is_order_flow": True,
                    "retrieved_chunks": 0
                }

            except Exception as e:
                logger.error(f"❌ ROUTER order workflow error: {e}", exc_info=True)

        if route == "sav":
            from app.core.database import get_database

            last_bot_message2 = ""
            if history_router:
                for msg in reversed(history_router):
                    if msg.get("role") == "assistant":
                        last_bot_message2 = (msg.get("content") or "")
                        break

            db = get_database()
            last_order = db["orders"].find_one({"session_id": session_id}, sort=[("created_at", -1)])

            sav_category_result = classify_sav_category(
                query=query,
                state=state,
                last_order_exists=bool(last_order),
                last_bot_message=last_bot_message2,
                history=history_router
            )
            sav_category = sav_category_result.get("category", "unknown")
            sav_category_confidence = float(sav_category_result.get("confidence") or 0.0)
            sav_category_reason = sav_category_result.get("reason") or ""

            if sav_category == "unknown":
                menu_msg = (
                    "D’accord. Quel type de demande ?\n"
                    "1) Retour\n2) Échange\n3) Remboursement\n4) Livraison\n5) Défaut produit\n\n"
                    "Répondez par un chiffre."
                )

                add_message(session_id, "user", query)
                add_message(session_id, "assistant", menu_msg)

                save_conversation(
                    session_id=session_id,
                    channel=channel,
                    user_message=query,
                    ai_response=menu_msg,
                    confidence=max(0.85, route_confidence),
                    escalated=False
                )

                return {
                    "message_id": str(uuid.uuid4()),
                    "answer": menu_msg,
                    "final_answer": menu_msg,
                    "intent": "sav_waiting_category",
                    "route": route,
                    "route_confidence": route_confidence,
                    "route_reason": route_reason,
                    "sav_category": sav_category,
                    "sav_category_confidence": sav_category_confidence,
                    "sav_category_reason": sav_category_reason,
                    "confidence": max(0.85, route_confidence),
                    "confidence_score": max(0.85, route_confidence),
                    "should_escalate": False,
                    "needs_human_agent": False,
                    "escalation_reason": None,
                    "session_id": session_id,
                    "conversation_state": "sav_waiting_category",
                    "is_order_flow": False,
                    "retrieved_chunks": 0
                }

            sav_query = "" if (query or "").strip().isdigit() else query
            history_for_sav2 = get_history(session_id, last_n=8)

            sav_answer = build_sav_reply(
                category=sav_category,
                last_order=last_order,
                user_text=sav_query,
                last_bot_text=last_bot_message2,
                history=history_for_sav2,
                session_id=session_id,
                channel=channel,
            )

            add_message(session_id, "user", query)
            add_message(session_id, "assistant", sav_answer)

            save_conversation(
                session_id=session_id,
                channel=channel,
                user_message=query,
                ai_response=sav_answer,
                confidence=max(0.75, route_confidence),
                escalated=False
            )
            _append_sav_thread_if_ticket_exists(session_id=session_id, channel=channel, user_text=query, assistant_text=sav_answer)

            next_state = "idle" if _should_close_sav_flow(sav_answer) else f"sav_{sav_category}"
            return {
                "message_id": str(uuid.uuid4()),
                "answer": sav_answer,
                "final_answer": sav_answer,
                "intent": "sav_flow",
                "route": route,
                "route_confidence": route_confidence,
                "route_reason": route_reason,
                "sav_category": sav_category,
                "sav_category_confidence": sav_category_confidence,
                "sav_category_reason": sav_category_reason,
                "confidence": max(0.75, route_confidence),
                "confidence_score": max(0.75, route_confidence),
                "should_escalate": False,
                "needs_human_agent": False,
                "escalation_reason": None,
                "session_id": session_id,
                "conversation_state": next_state,
                "is_order_flow": False,
                "retrieved_chunks": 0
            }

        embedding = embed_texts([query])[0]
        results = search_chunks(embedding, intent=intent, top_k=5)
        entities = extract_entities(query, results)
        answer = generate_response(query, results, session_id=session_id)
        
        # 📦 Track products mentioned
        track_product_mention(query, answer, session_id, intent)

        if is_howto and mentions_sav_topic:
            answer += "\n\nSouhaitez-vous que je lance la demande maintenant ? (Oui/Non)"
            next_state = "awaiting_sav_launch_confirmation"
        else:
            next_state = "idle"

        add_message(session_id, "user", query)
        add_message(session_id, "assistant", answer)

        confidence = compute_confidence(results, answer, intent)
        save_conversation(
            session_id=session_id,
            channel=channel,
            user_message=query,
            ai_response=answer,
            confidence=confidence,
            escalated=False
        )

        return {
            "message_id": str(uuid.uuid4()),
            "answer": answer,
            "intent": intent,
            "route": route,
            "route_confidence": route_confidence,
            "route_reason": route_reason,
            "entities": entities,
            "retrieved_knowledge": results,
            "final_answer": answer,
            "confidence": confidence,
            "confidence_score": confidence,
            "should_escalate": False,
            "needs_human_agent": False,
            "escalation_reason": None,
            "session_id": session_id,
            "conversation_state": next_state,
            "is_order_flow": False,
            "retrieved_chunks": len(results) if results else 0
        }
    # 🔥 ÉTAPE 4 : GESTION DES WORKFLOWS EXISTANTS OU DEMANDES EXPLICITES
    # ✅ Mots-clés EXPLICITES de commande seulement (pas de heuristiques de produits)
    explicit_order_keywords = [
        "commander", "acheter", "panier", "finaliser", "valider",
        "je veux commander", "je voudrais commander", "je souhaite commander"
    ]
    has_explicit_order_keyword = any(keyword in q for keyword in explicit_order_keywords)
    
    has_sav_words = any(w in q for w in ["sav", "retour", "retourner", "échange", "echanger", "échanger", "remboursement", "rembourser", "annuler", "réclamation", "reclamation", "plainte"])
    
    # ✅ Only start workflow for explicit order keywords, not product mentions alone
    should_start_workflow = (
        (not is_in_sav_flow) and
        (not is_in_choice_flow) and
        (not has_sav_words) and
        (is_in_order_workflow or has_explicit_order_keyword)
    )

    logger.info(f"""
🔍 WORKFLOW DECISION (explicit keywords only):
- is_in_order_workflow: {is_in_order_workflow}
- has_explicit_order_keyword: {has_explicit_order_keyword}
→ DECISION: {should_start_workflow}
""")

    # 🔥 SI WORKFLOW EXPLICITE DÉTECTÉ → LANCER
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
            if (new_state or "").strip().lower() == "order_placed":
                new_state = "idle"
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
            # Continue avec SAV/RAG si le workflow casse

    # ✅ ÉTAPE 5 : HOWTO SAV AVANT SAV OPÉRATIONNEL
    if (not is_in_order_workflow) and (not is_in_sav_flow) and (not is_in_choice_flow) and is_howto and mentions_sav_topic:
        embedding = embed_texts([query])[0]
        results = search_chunks(embedding, intent=intent, top_k=5)
        entities = extract_entities(query, results)
        answer = generate_response(query, results, session_id=session_id)
        answer += "\n\nSouhaitez-vous que je lance la demande maintenant ? (Oui/Non)"

        add_message(session_id, "user", query)
        add_message(session_id, "assistant", answer)

        confidence = compute_confidence(results, answer, intent)
        save_conversation(
            session_id=session_id,
            channel=channel,
            user_message=query,
            ai_response=answer,
            confidence=confidence,
            escalated=False
        )

        return {
            "message_id": str(uuid.uuid4()),
            "answer": answer,
            "final_answer": answer,
            "intent": intent,
            "entities": entities,
            "retrieved_knowledge": results,
            "confidence": confidence,
            "confidence_score": confidence,
            "should_escalate": False,
            "needs_human_agent": False,
            "escalation_reason": None,
            "session_id": session_id,
            "conversation_state": "awaiting_sav_launch_confirmation",
            "is_order_flow": False,
            "retrieved_chunks": len(results) if results else 0
        }

    # ✅ ÉTAPE 6 : SAV (uniquement si PAS en workflow commande)
    # Ici, on est sûr qu'on n'est PAS en plein flux de collecte (adresse, tel, etc.)
    from app.core.database import get_database

    history4 = get_history(session_id, last_n=4)
    last_bot_message2 = ""
    if history4:
        for msg in reversed(history4):
            if msg.get("role") == "assistant":
                last_bot_message2 = (msg.get("content") or "")
                break
    

    # ✅ Plainte/problème -> SAV
    complaint_markers = [
    "problème", "probleme", "souci", "erreur", "incorrect", "mauvais",
    "pas reçu", "pas recu", "non reçu", "non recu", "jamais reçu", "jamais recu",
    "retard", "en retard", "bloqué", "bloque",
    "abîmé", "abime", "cassé", "casse", "défectueux", "defectueux",
    "remboursement", "rembourser", "annuler", "réclamation", "reclamation", "plainte"
    ]
    is_complaint = any(w in q for w in complaint_markers)

    # ✅ Demande SAV explicite -> SAV
    explicit_sav_markers = ["sav", "retour", "retourner", "échange", "echanger", "échanger", "remboursement", "rembourser", "annuler"]
    explicit_sav_request = explicit_sav_action
    # ✅ Autoriser SAV uniquement si : déjà en SAV, ou plainte, ou demande SAV explicite
    allow_sav_detection = is_in_sav_flow or (state == "sav_waiting_category") or is_complaint or explicit_sav_request
    sav_category = None
    if allow_sav_detection:
        sav_category = detect_sav_category(query, last_bot_message2)

    if sav_category:
        db = get_database()
        last_order = db["orders"].find_one(
            {"session_id": session_id},
            sort=[("created_at", -1)]
        )

        history_for_sav3 = get_history(session_id, last_n=8)
        sav_answer = build_sav_reply(
            category=sav_category,
            last_order=last_order,
            user_text=query,
            last_bot_text=last_bot_message2,
            history=history_for_sav3,
            session_id=session_id,
            channel=channel,
        )

        add_message(session_id, "user", query)
        add_message(session_id, "assistant", sav_answer)

        save_conversation(
            session_id=session_id,
            channel=channel,
            user_message=query,
            ai_response=sav_answer,
            confidence=0.8,
            escalated=False
        )
        _append_sav_thread_if_ticket_exists(session_id=session_id, channel=channel, user_text=query, assistant_text=sav_answer)
        sav_done = _should_close_sav_flow(sav_answer)
        next_state = "idle" if sav_done else f"sav_{sav_category}"
        return {
            "message_id": str(uuid.uuid4()),
            "answer": sav_answer,
            "final_answer": sav_answer,
            "intent": "sav_flow",
            "confidence": 0.8,
            "confidence_score": 0.8,
            "should_escalate": False,
            "needs_human_agent": False,
            "escalation_reason": None,
            "session_id": session_id,
            "conversation_state": next_state,
            "is_order_flow": False,
            "retrieved_chunks": 0
        }
    
    # 🔥 ÉTAPE 7 : FLUX RAG CLASSIQUE
    embedding = embed_texts([query])[0]
    results = search_chunks(embedding, intent=intent, top_k=5)
    entities = extract_entities(query, results)
    answer = generate_response(query, results, session_id=session_id)
    
    # 📦 Track products mentioned in product-related queries (for implicit ordering)
    track_product_mention(query, answer, session_id, intent)

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
        return False
    
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
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send WhatsApp message: {e}")
        return False


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
        return False
    
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
        return True
    except Exception as e:
        logger.error(f"❌ Failed to send Facebook message: {e}")
        return False


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


class CustomerAccessRequest(BaseModel):
    channel: Optional[str] = None
    customer_id: Optional[str] = None
    order_id: Optional[str] = None
    phone_last4: Optional[str] = None


@app.post("/customer/access-token")
def issue_customer_access_token(payload: CustomerAccessRequest):
    from app.core.database import get_database

    db = get_database()
    customer_identifier = None
    resolved_order_id = payload.order_id

    if payload.order_id:
        order = db["orders"].find_one({"order_id": payload.order_id})
        if not order:
            raise HTTPException(status_code=404, detail="Order not found")

        phone = "".join(ch for ch in str(order.get("customer", {}).get("phone", "")) if ch.isdigit())
        provided_last4 = "".join(ch for ch in str(payload.phone_last4 or "") if ch.isdigit())
        if len(provided_last4) < 4 or not phone.endswith(provided_last4[-4:]):
            raise HTTPException(status_code=401, detail="Verification failed")

        customer_identifier = order.get("customer_identifier") or build_customer_identifier(
            order.get("channel", "web"),
            order.get("session_id", ""),
        )
    else:
        if not payload.channel or not payload.customer_id:
            raise HTTPException(status_code=400, detail="Provide either order_id+phone_last4 or channel+customer_id")

        customer_identifier = parse_customer_identifier(payload.channel, payload.customer_id)
        known_orders = db["orders"].count_documents({"customer_identifier": customer_identifier})
        known_tickets = db["sav_tickets"].count_documents({"customer_identifier": customer_identifier})
        if (known_orders + known_tickets) == 0:
            raise HTTPException(status_code=404, detail="No customer updates found for this identifier")

    token = issue_customer_token(
        {
            "scope": "customer_updates:read",
            "customer_identifier": customer_identifier,
            "order_id": resolved_order_id,
        },
        ttl_minutes=60,
    )

    return {
        "token": token,
        "expires_in_minutes": 60,
        "customer_identifier": customer_identifier,
        "order_id": resolved_order_id,
    }


@app.get("/customer/order-status")
def customer_order_status(
    order_id: Optional[str] = None,
    x_customer_token: str = Header(None)
):
    if not x_customer_token:
        raise HTTPException(status_code=401, detail="Missing customer token")

    try:
        token_data = validate_customer_token(x_customer_token)
    except ValueError as err:
        raise HTTPException(status_code=401, detail=str(err))

    if token_data.get("scope") != "customer_updates:read":
        raise HTTPException(status_code=403, detail="Invalid token scope")

    token_order = token_data.get("order_id")
    if token_order and order_id and token_order != order_id:
        raise HTTPException(status_code=403, detail="Token is restricted to another order")

    order = get_latest_order_snapshot(token_data.get("customer_identifier"), order_id=order_id or token_order)
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")

    history = order.get("status_history") or []
    latest_status = history[-1] if history else {}
    tracking_number = order.get("tracking_number") or latest_status.get("tracking_number")
    public_message = format_customer_update({
        "kind": "order_status",
        "status": order.get("status"),
    })
    response = {
        "status": normalize_order_status(order.get("status", "pending")),
        "tracking_number": tracking_number,
        "last_updated_at": _serialize_datetime_fields({"ts": order.get("updated_at")}).get("ts"),
        "latest_message": public_message,
        "message": public_message,
    }
    return _serialize_datetime_fields(response)


@app.get("/customer/sav-status")
def customer_sav_status(
    ticket_id: Optional[str] = None,
    order_id: Optional[str] = None,
    x_customer_token: str = Header(None)
):
    if not x_customer_token:
        raise HTTPException(status_code=401, detail="Missing customer token")

    try:
        token_data = validate_customer_token(x_customer_token)
    except ValueError as err:
        raise HTTPException(status_code=401, detail=str(err))

    if token_data.get("scope") != "customer_updates:read":
        raise HTTPException(status_code=403, detail="Invalid token scope")

    token_order = token_data.get("order_id")
    if token_order and order_id and token_order != order_id:
        raise HTTPException(status_code=403, detail="Token is restricted to another order")

    ticket = None
    if ticket_id:
        ticket = get_latest_sav_snapshot(token_data.get("customer_identifier"), order_id=order_id or token_order)
        if ticket and ticket.get("ticket_id") != ticket_id:
            ticket = None
    else:
        ticket = get_latest_sav_snapshot(token_data.get("customer_identifier"), order_id=order_id or token_order)

    if not ticket:
        raise HTTPException(status_code=404, detail="SAV ticket not found")

    history = ticket.get("status_history") or []
    latest_status = history[-1] if history else {}
    public_message = format_customer_update({
        "kind": "sav_status",
        "status": ticket.get("status"),
    })

    response = {
        "status": normalize_sav_status(ticket.get("status", "open")),
        "last_updated_at": _serialize_datetime_fields({"ts": ticket.get("updated_at")}).get("ts"),
        "latest_admin_message": format_customer_update({"kind": "sav_message"}),
        "message": public_message,
    }
    return _serialize_datetime_fields(response)


@app.get("/customer/updates")
def get_customer_updates(
    order_id: Optional[str] = None,
    since: Optional[str] = None,
    cursor: Optional[str] = None,
    session_id: Optional[str] = None,
    channel: Optional[str] = "web",
    x_customer_token: str = Header(None),
    x_customer_session: Optional[str] = Header(None),
):
    token_data: Optional[Dict[str, Any]] = None
    customer_identifier = ""

    if x_customer_token:
        try:
            token_data = validate_customer_token(x_customer_token)
        except ValueError as err:
            raise HTTPException(status_code=401, detail=str(err))

        if token_data.get("scope") != "customer_updates:read":
            raise HTTPException(status_code=403, detail="Invalid token scope")

        customer_identifier = token_data.get("customer_identifier") or ""
    else:
        resolved_session = (session_id or x_customer_session or "").strip()
        channel_norm = (channel or "web").strip().lower()
        if not resolved_session:
            raise HTTPException(status_code=401, detail="Missing customer token or session context")
        customer_identifier = build_customer_identifier(channel_norm, resolved_session)

    token_order = (token_data or {}).get("order_id")
    if token_order and order_id and token_order != order_id:
        raise HTTPException(status_code=403, detail="Token is restricted to another order")

    data = collect_customer_updates(
        customer_identifier=customer_identifier,
        order_id=order_id or token_order,
        since=since or cursor,
        include_internal=False,
    )

    latest_order = get_latest_order_snapshot(customer_identifier, order_id=order_id or token_order)
    latest_ticket = get_latest_sav_snapshot(customer_identifier, order_id=order_id or token_order)

    latest_order_message = ""
    latest_ticket_message = ""
    if latest_order:
        latest_order_message = format_customer_update({
            "kind": "order_status",
            "status": latest_order.get("status"),
        })
    if latest_ticket:
        latest_ticket_message = format_customer_update({
            "kind": "sav_status",
            "status": latest_ticket.get("status"),
        })

    timeline_updates: List[Dict[str, Any]] = []
    for update in data.get("updates", []):
        kind = (update.get("kind") or "").strip().lower()
        created_at = update.get("timestamp")

        if kind == "order_status":
            timeline_updates.append({
                "id": update.get("dedupe_key") or f"order:{update.get('order_id')}:{created_at}",
                "type": "order",
                "order_id": update.get("order_id"),
                "status": (update.get("status") or "").strip().lower(),
                "message": update.get("message") or "Your order has been updated.",
                "created_at": created_at,
                "kind": kind,
                "message_type": update.get("message_type") or "system_update",
            })
            continue

        if kind == "sav_status":
            sav_status = (update.get("status") or "").strip().lower()
            if sav_status in {"canceled", "cancelled"}:
                sav_status = "closed"
            timeline_updates.append({
                "id": update.get("dedupe_key") or f"sav:{update.get('ticket_id')}:{created_at}",
                "type": "sav",
                "ticket_id": update.get("ticket_id"),
                "status": sav_status,
                "message": update.get("message") or "Your SAV request has been updated.",
                "created_at": created_at,
                "kind": kind,
                "message_type": update.get("message_type") or "system_update",
            })
            continue

        timeline_updates.append({
            "id": update.get("dedupe_key") or f"message:{update.get('ticket_id')}:{created_at}",
            "type": "message",
            "source": "admin",
            "ticket_id": update.get("ticket_id"),
            "status": (update.get("status") or "").strip().lower(),
            "message": (update.get("raw_text") or update.get("message") or "").strip(),
            "created_at": created_at,
            "kind": kind or "sav_message",
            "message_type": update.get("message_type") or "public_reply",
        })

    return {
        "customer_identifier": data.get("customer_identifier"),
        "order_id": order_id or token_order,
        "updates": timeline_updates,
        "next_cursor": data.get("next_cursor"),
        "latest_order": _serialize_datetime_fields({
            "status": normalize_order_status((latest_order or {}).get("status", "pending")) if latest_order else "",
            "tracking_number": (latest_order or {}).get("tracking_number"),
            "last_updated_at": (latest_order or {}).get("updated_at"),
            "message": latest_order_message,
        }),
        "latest_ticket": _serialize_datetime_fields({
            "status": normalize_sav_status((latest_ticket or {}).get("status", "open")) if latest_ticket else "",
            "last_updated_at": (latest_ticket or {}).get("updated_at"),
            "message": latest_ticket_message,
        }),
    }


@app.get("/admin/customer-updates")
def get_customer_updates_admin(
    customer_identifier: str,
    order_id: Optional[str] = None,
    since: Optional[str] = None,
    cursor: Optional[str] = None,
    x_api_key: str = Header(None),
):
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    data = collect_customer_updates(
        customer_identifier=customer_identifier,
        order_id=order_id,
        since=since or cursor,
        include_internal=True,
    )

    latest_order = get_latest_order_snapshot(customer_identifier, order_id=order_id)
    latest_ticket = get_latest_sav_snapshot(customer_identifier, order_id=order_id)

    return {
        "customer_identifier": customer_identifier,
        "order_id": order_id,
        "updates": data.get("updates", []),
        "next_cursor": data.get("next_cursor"),
        "latest_order": _serialize_datetime_fields(latest_order or {}),
        "latest_ticket": _serialize_datetime_fields(latest_ticket or {}),
        "orders": [_serialize_datetime_fields(o) for o in data.get("orders", [])],
        "tickets": [_serialize_datetime_fields(t) for t in data.get("tickets", [])],
    }


@app.get("/admin/audit-logs")
def list_admin_audit_logs(limit: int = 100, api_key: str = Security(verify_api_key)):
    from app.core.database import get_database

    db = get_database()
    logs = list(db["admin_audit_logs"].find({}, {"_id": 0}).sort("created_at", -1).limit(limit))
    return {"total": len(logs), "logs": [_serialize_datetime_fields(log) for log in logs]}


# =====================================================
# 📊 ADMIN ENDPOINTS (ALL SECURED)
# =====================================================

# 📊 ADMIN: CONVERSATIONS TABLE (✅ CORRIGÉ)
@app.get("/admin/conversations")
def admin_table_data(
    escalated_only: Optional[bool] = False,
    channel: Optional[str] = None,
    x_api_key: str = Header(None)
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

@app.get("/admin/sav-tickets")
def admin_sav_tickets(limit: int = 50, x_api_key: str = Header(None)):
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    from app.core.sav_tickets import get_sav_collection

    col = get_sav_collection()
    tickets = list(
        col.find({}, {"_id": 0})
        .sort("updated_at", -1)
        .limit(int(limit))
    )
    return {"total": len(tickets), "tickets": tickets}


@app.put("/admin/sav-tickets/{ticket_id}/status")
def admin_update_sav_ticket_status(
    ticket_id: str,
    status: str,
    reason: str = "",
    x_api_key: str = Header(None),
    x_admin_user: str = Header("admin")
):
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    valid = ["open", "in_progress", "resolved", "canceled", "waiting_user", "waiting_customer", "cancelled"]
    if status not in valid:
        raise HTTPException(status_code=400, detail=f"Invalid status. Use: {', '.join(valid)}")

    from app.core.sav_tickets import update_sav_ticket_status, get_sav_collection

    col = get_sav_collection()
    before = col.find_one({"ticket_id": ticket_id}, {"_id": 0})
    ticket = update_sav_ticket_status(ticket_id, status, reason=reason, changed_by=x_admin_user)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    if "_id" in ticket:
        del ticket["_id"]

    log_admin_action(
        action="sav_status_updated",
        resource_type="sav_ticket",
        resource_id=ticket_id,
        admin_user=x_admin_user,
        reason=reason,
        before=before,
        after=ticket,
    )
    try:
        _notify_sav_customer(ticket or {}, x_admin_user, event_kind="sav_status", admin_message=reason)
    except Exception:
        logger.exception("Failed to notify customer for SAV status update")

    return {"success": True, "ticket_id": ticket_id, "new_status": status, "reason": reason}
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
# 📦 ORDERS ENDPOINTS - ADMIN (🔥 MODIFIÉ)
# =====================================================

@app.get("/admin/orders")
def admin_orders(
    limit: int = 50,
    status: Optional[str] = None,
    x_api_key: str = Header(None)
):
    """
    Get all orders for admin dashboard (🔥 AVEC coordonnées correctes)
    """
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    
    db = get_database()
    orders_collection = db["orders"]
    
    query = {}
    if status:
        query["status"] = status
    
    orders_raw = list(
        orders_collection
        .find(query, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )
    
    # 🔥 FORMATER LES DONNÉES CORRECTEMENT
    formatted_orders = []
    for order in orders_raw:
        customer = order.get("customer", {})
        
        formatted_orders.append({
            "order_id": order.get("order_id"),
            # 🔥 CORRIGER : Extraire correctement les coordonnées
            "customer_name": customer.get("name", "Inconnu"),
            "customer_phone": customer.get("phone", "N/A"),
            "customer_address": customer.get("address", "N/A"),
            "items": order.get("items", []),
            "subtotal": order.get("subtotal", 0),
            "delivery_fee": order.get("delivery_fee", 0),
            "total_price": order.get("total", 0),
            "payment_method": order.get("payment_method", "cash_on_delivery"),
            "status": order.get("status", "pending"),
            "channel": order.get("channel", "web"),
            "session_id": order.get("session_id"),
            "created_at": order.get("created_at"),
            "updated_at": order.get("updated_at")
        })
    
    return {
        "total": len(formatted_orders),
        "orders": formatted_orders
    }


@app.get("/admin/orders/stats")
def admin_orders_stats(x_api_key: str = Header(None)):
    """
    🔥 Get orders statistics for dashboard (AVEC KPIs AVANCÉS)
    """
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    
    db = get_database()
    orders_collection = db["orders"]
    
    # 🔥 COMPTEURS PAR STATUT
    total_orders = orders_collection.count_documents({})
    delivered_count = orders_collection.count_documents({"status": "delivered"})
    confirmed_count = orders_collection.count_documents({"status": "confirmed"})
    shipped_count = orders_collection.count_documents({"status": "shipped"})
    pending_count = orders_collection.count_documents({"status": "pending"})
    cancelled_count = orders_collection.count_documents({"status": "cancelled"})
    
    # 🔥 CA ENCAISSÉ (delivered uniquement)
    pipeline_delivered = [
        {"$match": {"status": "delivered"}},
        {"$group": {
            "_id": None,
            "total_revenue": {"$sum": "$total"}
        }}
    ]
    delivered_revenue_result = list(orders_collection.aggregate(pipeline_delivered))
    delivered_revenue = delivered_revenue_result[0]["total_revenue"] if delivered_revenue_result else 0
    
    # 🔥 CA EN TRANSIT (confirmed + shipped)
    pipeline_transit = [
        {"$match": {"status": {"$in": ["confirmed", "shipped"]}}},
        {"$group": {
            "_id": None,
            "total_revenue": {"$sum": "$total"}
        }}
    ]
    transit_revenue_result = list(orders_collection.aggregate(pipeline_transit))
    transit_revenue = transit_revenue_result[0]["total_revenue"] if transit_revenue_result else 0
    
    # 🔥 CA TOTAL (delivered + transit)
    total_revenue = delivered_revenue + transit_revenue
    
    # 🔥 COMMANDES ACTIVES (total - cancelled)
    active_orders = total_orders - cancelled_count
    
    # 🔥 COMMANDES EN COURS (confirmed + shipped + pending)
    in_progress_count = confirmed_count + shipped_count + pending_count
    
    # 🔥 PANIER MOYEN (sur commandes actives)
    avg_order_value = total_revenue / active_orders if active_orders > 0 else 0
    
    return {
        # Compteurs
        "total_orders": total_orders,
        "active_orders": active_orders,
        "delivered_count": delivered_count,
        "in_progress_count": in_progress_count,
        "pending_count": pending_count,
        "confirmed_count": confirmed_count,
        "shipped_count": shipped_count,
        "cancelled_count": cancelled_count,
        
        # Revenus
        "delivered_revenue": round(delivered_revenue, 2),
        "transit_revenue": round(transit_revenue, 2),
        "total_revenue": round(total_revenue, 2),
        
        # Moyennes
        "average_order_value": round(avg_order_value, 2)
    }


# 🔥 NOUVEAU ENDPOINT : Mettre à jour le statut d'une commande
@app.put("/admin/orders/{order_id}/status")
def update_order_status(
    order_id: str,
    status: str,
    x_api_key: str = Header(None),
    x_admin_user: str = Header("admin")
):
    """
    Change le statut d'une commande
    
    Statuts possibles: pending, confirmed, shipped, delivered, cancelled
    """
    # Vérifier l'API Key
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    # Valider le statut
    valid_statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
    
    if status not in valid_statuses:
        raise HTTPException(
            status_code=400,
            detail=f"Statut invalide. Valeurs possibles : {', '.join(valid_statuses)}"
        )
    
    # Importer OrderManager
    from app.core.order_manager import OrderManager
    
    order_mgr = OrderManager()
    before = order_mgr.get_order(order_id)
    success = order_mgr.update_order_status(order_id, status, note="Status updated from dashboard", changed_by=x_admin_user)
    
    if not success:
        raise HTTPException(
            status_code=404,
            detail=f"Commande {order_id} introuvable"
        )
    
    logger.info(f"✅ Statut de la commande {order_id} changé à: {status}")
    after = order_mgr.get_order(order_id)
    log_admin_action(
        action="order_status_updated",
        resource_type="order",
        resource_id=order_id,
        admin_user=x_admin_user,
        reason="Status update",
        before=_serialize_datetime_fields(before or {}),
        after=_serialize_datetime_fields(after or {}),
    )
    
    return {
        "success": True,
        "order_id": order_id,
        "new_status": status,
        "message": f"Statut changé à '{status}'"
    }


# ============================================================================
# 📦 ENDPOINTS ADMIN ORDERS & SAV TICKETS (Complete CRUD)
# ============================================================================

@app.get("/admin/orders")
def list_orders(
    page: int = 1,
    limit: int = 20,
    status: str = None,
    q: str = None,
    from_date: str = None,
    to_date: str = None,
    x_api_key: str = Header(None)
):
    """
    List all orders with optional filtering.
    
    Query params:
    - page: Page number (1-indexed)
    - limit: Items per page (default 20)
    - status: Filter by status (pending/confirmed/shipped/delivered/cancelled)
    - q: Search by order_id or customer name/phone
    - from_date: YYYY-MM-DD format
    - to_date: YYYY-MM-DD format
    """
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    db = get_database()
    orders_col = db["orders"]
    
    # Build query
    query = {}
    if status:
        query["status"] = status
    
    if q:
        query["$or"] = [
            {"order_id": {"$regex": q, "$options": "i"}},
            {"customer.name": {"$regex": q, "$options": "i"}},
            {"customer.phone": {"$regex": q, "$options": "i"}},
        ]
    
    if from_date or to_date:
        date_query = {}
        if from_date:
            date_query["$gte"] = datetime.strptime(from_date, "%Y-%m-%d")
        if to_date:
            date_query["$lte"] = datetime.strptime(to_date, "%Y-%m-%d")
        if date_query:
            query["created_at"] = date_query
    
    # Count total
    total = orders_col.count_documents(query)
    
    # Paginate
    skip = (page - 1) * limit
    orders = list(orders_col.find(query).sort("created_at", -1).skip(skip).limit(limit))
    
    # Serialize
    for order in orders:
        if "_id" in order:
            del order["_id"]
        if isinstance(order.get("created_at"), datetime):
            order["created_at"] = order["created_at"].isoformat()
        if isinstance(order.get("updated_at"), datetime):
            order["updated_at"] = order["updated_at"].isoformat()
        if order.get("status_history"):
            for item in order["status_history"]:
                if isinstance(item.get("changed_at"), datetime):
                    item["changed_at"] = item["changed_at"].isoformat()
    
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "orders": orders
    }


@app.get("/admin/orders/{order_id}")
def get_order_detail(order_id: str, x_api_key: str = Header(None)):
    """Get order details with linked SAV tickets"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    db = get_database()
    
    order = db["orders"].find_one({"order_id": order_id})
    if not order:
        raise HTTPException(status_code=404, detail="Order not found")
    
    if "_id" in order:
        del order["_id"]
    if isinstance(order.get("created_at"), datetime):
        order["created_at"] = order["created_at"].isoformat()
    if isinstance(order.get("updated_at"), datetime):
        order["updated_at"] = order["updated_at"].isoformat()
    if order.get("status_history"):
        for item in order["status_history"]:
            if isinstance(item.get("changed_at"), datetime):
                item["changed_at"] = item["changed_at"].isoformat()
    
    # Get linked SAV tickets
    sav_tickets = list(db["sav_tickets"].find({"order_id": order_id}))
    for ticket in sav_tickets:
        if "_id" in ticket:
            del ticket["_id"]
        for field in ["created_at", "updated_at"]:
            if isinstance(ticket.get(field), datetime):
                ticket[field] = ticket[field].isoformat()
        if ticket.get("status_history"):
            for item in ticket["status_history"]:
                if isinstance(item.get("changed_at"), datetime):
                    item["changed_at"] = item["changed_at"].isoformat()
    
    order["sav_tickets"] = sav_tickets
    return order


@app.post("/admin/orders/{order_id}/status")
def change_order_status(
    order_id: str,
    status: str,
    note: str = "",
    x_api_key: str = Header(None),
    x_admin_user: str = Header("admin")
):
    """Update order status with history tracking"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    valid_statuses = ["pending", "confirmed", "shipped", "delivered", "cancelled"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    from app.core.order_manager import OrderManager
    order_mgr = OrderManager()
    before = order_mgr.get_order(order_id)
    
    success = order_mgr.update_order_status(order_id, status, note=note, changed_by=x_admin_user)
    if not success:
        raise HTTPException(status_code=404, detail="Order not found")
    
    order = order_mgr.get_order(order_id)
    log_admin_action(
        action="order_status_updated",
        resource_type="order",
        resource_id=order_id,
        admin_user=x_admin_user,
        reason=note,
        before=_serialize_datetime_fields(before or {}),
        after=_serialize_datetime_fields(order or {}),
    )
    try:
        _notify_order_customer(order or {}, x_admin_user, reason=note)
    except Exception:
        logger.exception("Failed to notify customer for order status update")
    if "_id" in order:
        del order["_id"]
    if isinstance(order.get("created_at"), datetime):
        order["created_at"] = order["created_at"].isoformat()
    if isinstance(order.get("updated_at"), datetime):
        order["updated_at"] = order["updated_at"].isoformat()
    if order.get("status_history"):
        for item in order["status_history"]:
            if isinstance(item.get("changed_at"), datetime):
                item["changed_at"] = item["changed_at"].isoformat()
    
    return order


@app.post("/admin/orders/{order_id}/tracking")
def set_tracking_number(
    order_id: str,
    tracking_number: str,
    x_api_key: str = Header(None),
    x_admin_user: str = Header("admin")
):
    """Set tracking number for a shipped order"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.order_manager import OrderManager
    order_mgr = OrderManager()
    before = order_mgr.get_order(order_id)
    
    success = order_mgr.update_tracking_number(order_id, tracking_number)
    if not success:
        raise HTTPException(status_code=404, detail="Order not found")

    after = order_mgr.get_order(order_id)
    log_admin_action(
        action="order_tracking_updated",
        resource_type="order",
        resource_id=order_id,
        admin_user=x_admin_user,
        reason="Tracking number updated",
        before=_serialize_datetime_fields(before or {}),
        after=_serialize_datetime_fields(after or {}),
        metadata={"tracking_number": tracking_number},
    )
    try:
        _notify_order_customer(after or {}, x_admin_user, reason="Tracking number updated")
    except Exception:
        logger.exception("Failed to notify customer for tracking update")
    
    return {
        "success": True,
        "order_id": order_id,
        "tracking_number": tracking_number
    }


@app.get("/admin/sav-tickets")
def list_sav_tickets(
    page: int = 1,
    limit: int = 20,
    status: str = None,
    category: str = None,
    q: str = None,
    x_api_key: str = Header(None)
):
    """
    List SAV tickets with optional filtering.
    
    Query params:
    - page: Page number (1-indexed)
    - limit: Items per page (default 20)
    - status: open/in_progress/waiting_customer/resolved/cancelled
    - category: exchange_return/delivery_issue/refund_cancel/defective
    - q: Search by ticket_id or order_id
    """
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    db = get_database()
    sav_col = db["sav_tickets"]
    
    query = {}
    if status:
        query["status"] = status
    if category:
        query["category"] = category
    if q:
        query["$or"] = [
            {"ticket_id": {"$regex": q, "$options": "i"}},
            {"order_id": {"$regex": q, "$options": "i"}},
        ]
    
    total = sav_col.count_documents(query)
    skip = (page - 1) * limit
    tickets = list(sav_col.find(query).sort("updated_at", -1).skip(skip).limit(limit))
    
    # Serialize
    for ticket in tickets:
        if "_id" in ticket:
            del ticket["_id"]
        for field in ["created_at", "updated_at"]:
            if isinstance(ticket.get(field), datetime):
                ticket[field] = ticket[field].isoformat()
        if ticket.get("status_history"):
            for item in ticket["status_history"]:
                if isinstance(item.get("changed_at"), datetime):
                    item["changed_at"] = item["changed_at"].isoformat()
        if ticket.get("messages_thread"):
            for msg in ticket["messages_thread"]:
                if isinstance(msg.get("created_at"), datetime):
                    msg["created_at"] = msg["created_at"].isoformat()
    
    return {
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit,
        "tickets": tickets
    }


@app.get("/admin/sav-tickets/{ticket_id}")
def get_sav_ticket_detail(ticket_id: str, x_api_key: str = Header(None)):
    """Get SAV ticket details"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.database import get_database
    db = get_database()
    
    ticket = db["sav_tickets"].find_one({"ticket_id": ticket_id})
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    
    if "_id" in ticket:
        del ticket["_id"]
    for field in ["created_at", "updated_at"]:
        if isinstance(ticket.get(field), datetime):
            ticket[field] = ticket[field].isoformat()
    if ticket.get("status_history"):
        for item in ticket["status_history"]:
            if isinstance(item.get("changed_at"), datetime):
                item["changed_at"] = item["changed_at"].isoformat()
    if ticket.get("messages_thread"):
        for msg in ticket["messages_thread"]:
            if isinstance(msg.get("created_at"), datetime):
                msg["created_at"] = msg["created_at"].isoformat()
    
    return ticket


@app.post("/admin/sav-tickets/{ticket_id}/status")
def change_sav_ticket_status(
    ticket_id: str,
    status: str,
    reason: str = "",
    x_api_key: str = Header(None),
    x_admin_user: str = Header("admin")
):
    """Change SAV ticket status and track history"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    valid_statuses = ["open", "in_progress", "waiting_customer", "resolved", "cancelled"]
    if status not in valid_statuses:
        raise HTTPException(status_code=400, detail=f"Invalid status: {status}")
    
    from app.core.sav_tickets import update_sav_ticket_status, get_sav_collection

    col = get_sav_collection()
    before = col.find_one({"ticket_id": ticket_id}, {"_id": 0})
    ticket = update_sav_ticket_status(ticket_id, status, reason=reason, changed_by=x_admin_user)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    raw_after = dict(ticket)
    if "_id" in raw_after:
        del raw_after["_id"]

    log_admin_action(
        action="sav_status_updated",
        resource_type="sav_ticket",
        resource_id=ticket_id,
        admin_user=x_admin_user,
        reason=reason,
        before=before,
        after=raw_after,
    )
    try:
        _notify_sav_customer(ticket or {}, x_admin_user, event_kind="sav_status", admin_message=reason)
    except Exception:
        logger.exception("Failed to notify customer for SAV status update")
    
    if "_id" in ticket:
        del ticket["_id"]
    for field in ["created_at", "updated_at"]:
        if isinstance(ticket.get(field), datetime):
            ticket[field] = ticket[field].isoformat()
    if ticket.get("status_history"):
        for item in ticket["status_history"]:
            if isinstance(item.get("changed_at"), datetime):
                item["changed_at"] = item["changed_at"].isoformat()
    
    return ticket


@app.post("/admin/sav-tickets/{ticket_id}/note")
def update_sav_ticket_note(
    ticket_id: str,
    internal_note: str = "",
    admin_action: str = "",
    resolution_note: str = "",
    send_resolution_to_customer: bool = False,
    x_api_key: str = Header(None),
    x_admin_user: str = Header("admin")
):
    """Update internal note and admin action for SAV ticket"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.sav_tickets import add_sav_ticket_note, get_sav_collection, add_sav_ticket_message_with_meta

    before = get_sav_collection().find_one({"ticket_id": ticket_id}, {"_id": 0})
    ticket = add_sav_ticket_note(ticket_id, internal_note, admin_action)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    after_for_audit = dict(ticket)
    if "_id" in after_for_audit:
        del after_for_audit["_id"]
    log_admin_action(
        action="sav_note_updated",
        resource_type="sav_ticket",
        resource_id=ticket_id,
        admin_user=x_admin_user,
        reason=admin_action,
        before=before,
        after=after_for_audit,
        metadata={"internal_note": internal_note},
    )

    if internal_note:
        ticket = add_sav_ticket_message_with_meta(
            ticket_id,
            "admin",
            internal_note,
            author=x_admin_user,
            delivery={},
            message_type="internal",
            visible_to_customer=False,
        ) or ticket

    if resolution_note:
        ticket = add_sav_ticket_message_with_meta(
            ticket_id,
            "admin",
            resolution_note,
            author=x_admin_user,
            delivery={},
            message_type="resolution",
            visible_to_customer=bool(send_resolution_to_customer),
        ) or ticket

        if send_resolution_to_customer:
            try:
                _notify_sav_customer(ticket or {}, x_admin_user, event_kind="sav_message", admin_message=resolution_note)
            except Exception:
                logger.exception("Failed to notify customer for SAV resolution note")
    
    if "_id" in ticket:
        del ticket["_id"]
    for field in ["created_at", "updated_at"]:
        if isinstance(ticket.get(field), datetime):
            ticket[field] = ticket[field].isoformat()
    
    return ticket


@app.post("/admin/sav-tickets/{ticket_id}/message")
def add_sav_ticket_message(
    ticket_id: str,
    content: str,
    message_type: str = "public_reply",
    send_to_customer: bool = True,
    x_api_key: str = Header(None),
    x_admin_user: str = Header("admin")
):
    """Add admin message to SAV ticket"""
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    
    from app.core.sav_tickets import get_sav_collection, add_sav_ticket_message_with_meta

    valid_message_types = {"internal", "public_reply", "system_update", "resolution"}
    if message_type not in valid_message_types:
        raise HTTPException(status_code=400, detail=f"Invalid message_type. Use: {', '.join(sorted(valid_message_types))}")

    col = get_sav_collection()
    ticket_before = col.find_one({"ticket_id": ticket_id})
    if not ticket_before:
        raise HTTPException(status_code=404, detail="Ticket not found")

    should_deliver_to_customer = bool(send_to_customer) and message_type in {"public_reply", "system_update", "resolution"}
    delivery = _deliver_admin_message(ticket_before, content, x_admin_user) if should_deliver_to_customer else {
        "delivery_id": "",
        "status": "skipped",
        "channel": (ticket_before.get("channel") or "web"),
        "recipient": "",
        "attempts": 0,
        "error": "",
    }
    ticket = add_sav_ticket_message_with_meta(
        ticket_id,
        "admin",
        content,
        author=x_admin_user,
        delivery=delivery,
        message_type=message_type,
        visible_to_customer=should_deliver_to_customer,
    )
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")

    after_for_audit = dict(ticket)
    if "_id" in after_for_audit:
        del after_for_audit["_id"]
    log_admin_action(
        action="sav_message_sent",
        resource_type="sav_ticket",
        resource_id=ticket_id,
        admin_user=x_admin_user,
        reason="Admin support message logged",
        before={"ticket_id": ticket_id},
        after=after_for_audit,
        metadata={"delivery": delivery, "message_type": message_type, "send_to_customer": should_deliver_to_customer},
    )
    if should_deliver_to_customer:
        try:
            _notify_sav_customer(ticket or {}, x_admin_user, event_kind="sav_message", admin_message=content)
        except Exception:
            logger.exception("Failed to notify customer for SAV admin message")
    
    if "_id" in ticket:
        del ticket["_id"]
    for field in ["created_at", "updated_at"]:
        if isinstance(ticket.get(field), datetime):
            ticket[field] = ticket[field].isoformat()
    if ticket.get("messages_thread"):
        for msg in ticket["messages_thread"]:
            if isinstance(msg.get("created_at"), datetime):
                msg["created_at"] = msg["created_at"].isoformat()
    
    ticket["last_delivery"] = delivery
    return ticket