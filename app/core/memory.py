# app/core/memory.py
from collections import defaultdict
from typing import List, Dict, Optional
from datetime import datetime, timedelta

# { session_id : [ {"role": "...", "content": "..."}, ... ] }
MEMORY_STORE: defaultdict[str, List[Dict[str, str]]] = defaultdict(list)

# Product context per session (for implicit order handling)
# { session_id: {
#     "last_candidates": [{"name": str, "source": str, "confidence": float, "ts": datetime}, ...],
#     "selected_product": Optional[str],  # when user explicitly chose
#     "last_updated_at": datetime
#   }
# }
PRODUCT_CONTEXT_STORE: defaultdict[str, Dict] = defaultdict(lambda: {
    "last_candidates": [],
    "selected_product": None,
    "last_updated_at": None
})

PRODUCT_CONTEXT_TTL_MINUTES = 10
MAX_PRODUCT_CANDIDATES = 3

def add_message(session_id: str, role: str, content: str) -> None:
    """
    role: 'user' | 'assistant'
    """
    MEMORY_STORE[session_id].append({"role": role, "content": content})

def get_history(session_id: str, last_n: int = 5) -> List[Dict[str, str]]:
    """
    Returns last N messages for the session
    """
    return MEMORY_STORE[session_id][-last_n:]


def add_product_candidate(
    session_id: str,
    product_name: str,
    source: str = "catalog",  # "catalog", "rag", "heuristic"
    confidence: float = 0.8
) -> None:
    """
    Add a product candidate to context (deduplicates by name).
    
    Args:
        session_id: User session ID
        product_name: Name of the product
        source: Where this candidate came from
        confidence: Confidence score (0.0-1.0)
    """
    ctx = PRODUCT_CONTEXT_STORE[session_id]
    
    # Remove old entry if exists (deduplicate)
    ctx["last_candidates"] = [
        c for c in ctx["last_candidates"] 
        if c["name"].lower() != product_name.lower()
    ]
    
    # Add new candidate
    ctx["last_candidates"].append({
        "name": product_name,
        "source": source,
        "confidence": confidence,
        "ts": datetime.utcnow()
    })
    
    # Keep only MAX_PRODUCT_CANDIDATES most recent
    ctx["last_candidates"] = ctx["last_candidates"][-MAX_PRODUCT_CANDIDATES:]
    ctx["last_updated_at"] = datetime.utcnow()


def get_product_context(session_id: str) -> Dict:
    """
    Get product context for session, removing expired candidates.
    
    Returns:
        Dict with "candidates" (valid list), "selected_product" (or None)
    """
    ctx = PRODUCT_CONTEXT_STORE[session_id]
    
    # Remove expired candidates (older than TTL)
    now = datetime.utcnow()
    ttl_cutoff = now - timedelta(minutes=PRODUCT_CONTEXT_TTL_MINUTES)
    
    valid_candidates = [
        c for c in ctx.get("last_candidates", [])
        if c.get("ts") and c["ts"] > ttl_cutoff
    ]
    
    # Update store
    ctx["last_candidates"] = valid_candidates
    
    # Check if selected_product is still recent
    selected = ctx.get("selected_product")
    if selected and ctx.get("last_updated_at"):
        if ctx["last_updated_at"] < ttl_cutoff:
            selected = None
    
    return {
        "candidates": valid_candidates,
        "selected_product": selected
    }


def set_product_selection(session_id: str, product_name: Optional[str]) -> None:
    """
    Mark a product as explicitly selected by the user.
    
    Args:
        session_id: User session ID
        product_name: Product name or None to clear
    """
    ctx = PRODUCT_CONTEXT_STORE[session_id]
    ctx["selected_product"] = product_name
    ctx["last_updated_at"] = datetime.utcnow()


def clear_product_context(session_id: str) -> None:
    """
    Clear all product context for a session (e.g., after order completed).
    """
    PRODUCT_CONTEXT_STORE[session_id] = {
        "last_candidates": [],
        "selected_product": None,
        "last_updated_at": None
    }
