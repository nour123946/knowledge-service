# app/core/memory.py
from collections import defaultdict
from typing import List, Dict

# { session_id : [ {"role": "...", "content": "..."}, ... ] }
MEMORY_STORE: defaultdict[str, List[Dict[str, str]]] = defaultdict(list)

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
