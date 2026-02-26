# app/core/feedback.py

from datetime import datetime
from app.core.database import db

# Collection MongoDB pour les feedbacks
feedback_collection = db["feedbacks"]

# Index pour performance
feedback_collection.create_index("session_id")
feedback_collection.create_index("created_at")
feedback_collection.create_index("rating")


def save_feedback(
    session_id: str,
    message_id: str,
    user_message: str,
    bot_response: str,
    rating: str,  # 'positive' or 'negative'
    comment: str = None,
    intent: str = None,
    confidence: float = None
):
    """
    Save user feedback to database
    
    Args:
        session_id: User session identifier
        message_id: Unique message identifier
        user_message: Original user question
        bot_response: Bot's answer
        rating: 'positive' or 'negative'
        comment: Optional user comment
        intent: Detected intent
        confidence: Confidence score
    """
    
    feedback_doc = {
        "message_id": message_id,
        "session_id": session_id,
        "user_message": user_message,
        "bot_response": bot_response,
        "rating": rating,
        "comment": comment,
        "intent": intent,
        "confidence": confidence,
        "created_at": datetime.utcnow()
    }
    
    feedback_collection.insert_one(feedback_doc)
    
    print(f"✅ Feedback saved: {rating} for session {session_id}")


def get_feedback_stats():
    """
    Get feedback statistics
    
    Returns:
        dict with total, positive, negative, satisfaction_rate
    """
    
    total = feedback_collection.count_documents({})
    positive = feedback_collection.count_documents({"rating": "positive"})
    negative = feedback_collection.count_documents({"rating": "negative"})
    
    satisfaction_rate = (positive / total * 100) if total > 0 else 0
    
    return {
        "total_feedbacks": total,
        "positive": positive,
        "negative": negative,
        "satisfaction_rate": round(satisfaction_rate, 2)
    }


def get_negative_feedbacks(limit: int = 50):
    """
    Get all negative feedbacks for review
    
    Args:
        limit: Maximum number of results
        
    Returns:
        List of negative feedback documents
    """
    
    return list(
        feedback_collection
        .find({"rating": "negative"}, {"_id": 0})
        .sort("created_at", -1)
        .limit(limit)
    )


def get_low_confidence_feedbacks(threshold: float = 0.5, limit: int = 50):
    """
    Get feedbacks with low confidence scores
    
    Args:
        threshold: Confidence threshold
        limit: Maximum results
        
    Returns:
        List of low confidence feedbacks
    """
    
    return list(
        feedback_collection
        .find({"confidence": {"$lt": threshold}}, {"_id": 0})
        .sort("confidence", 1)
        .limit(limit)
    )