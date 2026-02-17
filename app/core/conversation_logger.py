from datetime import datetime
from app.core.database import conversation_collection


def save_conversation(
    session_id: str,
    channel: str,
    user_message: str,
    ai_response: str,
    confidence: float,
    escalated: bool
):
    now = datetime.utcnow()

    # USER message
    message_user = {
        "role": "user",
        "message": user_message,
        "timestamp": now
    }

    # ASSISTANT message
    message_ai = {
        "role": "assistant",
        "message": ai_response,
        "confidence": float(confidence),
        "timestamp": now
    }

    existing = conversation_collection.find_one({"session_id": session_id})

    if existing:
        # ✅ si déjà escalated, on garde True
        new_escalation_state = existing.get("escalated", False) or escalated

        conversation_collection.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": {"$each": [message_user, message_ai]}},
                "$set": {
                    "updated_at": now,
                    "channel": channel,            # au cas où
                    "escalated": new_escalation_state,
                    "last_message": ai_response    # utile pour tableau admin
                },
                "$inc": {"message_count": 2}
            }
        )
    else:
        conversation_collection.insert_one({
            "session_id": session_id,
            "channel": channel,
            "created_at": now,
            "updated_at": now,
            "escalated": escalated,
            "message_count": 2,
            "last_message": ai_response,  # ✅ tableau admin
            "messages": [message_user, message_ai]
        })
