# Quick Reference: Implicit Order Handling API

## For Developers

### Using Product Context

#### Add a Product Candidate
```python
from app.core.memory import add_product_candidate

add_product_candidate(
    session_id="user-123",
    product_name="Adidas Ultraboost",
    source="catalog",  # or "rag", "query", "response", "user_selection"
    confidence=0.88
)
```

#### Get Product Context
```python
from app.core.memory import get_product_context

context = get_product_context(session_id="user-123")
print(context["candidates"])      # List of product candidates
print(context["selected_product"]) # Previously selected product or None
```

#### Set Product Selection
```python
from app.core.memory import set_product_selection

set_product_selection(session_id="user-123", product_name="Converse Chuck Taylor")
```

#### Clear Product Context (e.g., after order placed)
```python
from app.core.memory import clear_product_context

clear_product_context(session_id="user-123")
```

### Resolving Products for Orders

```python
from app.main import resolve_product_for_order

resolution = resolve_product_for_order(
    query="je veux commander",
    session_id="user-123"
)

# Handle based on status
if resolution["status"] == "direct":
    product_name = resolution["product_name"]
    # Add to cart automatically
    cart.add_item({"name": product_name})
    
elif resolution["status"] == "choose":
    options = resolution["options"]
    # Show menu: "Lequel ? 1. Option1 2. Option2"
    
elif resolution["status"] == "ask":
    # Ask: "Quel produit voulez-vous commander ?"
```

### Manual Product Tracking

```python
from app.main import track_product_mention

track_product_mention(
    query="quel est le prix du puma?",
    response="Puma RS-X para 310 TND...",
    session_id="user-123",
    intent="product_info"
)
```

## Configuration Constants

**Location**: `app/core/memory.py`

```python
PRODUCT_CONTEXT_TTL_MINUTES = 10      # Time before candidates expire
MAX_PRODUCT_CANDIDATES = 3            # Max candidates to store
```

**Location**: `app/main.py` (in resolve_product_for_order)

```python
PRODUCTS_CATALOG = {
    "puma": "Puma RS-X",
    "adidas": "Adidas Ultraboost",
    "converse": "Converse Chuck Taylor",
    "new balance": "New Balance 574",
}
```

## Workflow States

### Order Workflow States
```python
from app.workflows.order_workflow import STATES

STATES.ASKING_PRODUCT    # User must specify which product
STATES.CHOOSING_PRODUCT  # User chooses from options (1, 2, 3)
STATES.WAITING_NAME      # Collect customer name
STATES.WAITING_PHONE     # Collect phone number
STATES.WAITING_ADDRESS   # Collect delivery address
STATES.WAITING_PAYMENT   # Choose payment method
STATES.CONFIRM           # Confirm order
```

## Response Examples

### Direct Product Auto-Add
```json
{
  "status": "direct",
  "product_name": "Converse Chuck Taylor",
  "confidence": 0.88
}
```

### Present Choice Menu
```json
{
  "status": "choose",
  "options": ["Puma RS-X", "Adidas Ultraboost"],
  "confidence": 0.75
}
```

### Ask User for Product
```json
{
  "status": "ask",
  "confidence": 0.0
}
```

## Testing

### Run All Tests
```bash
cd c:\Users\Hp\Desktop\knowledge-service
. .\venv\Scripts\Activate.ps1
$env:PYTHONIOENCODING='utf-8'
python test_implicit_order.py
```

### Run Final End-to-End Test
```bash
python test_final_validation.py
```

## Troubleshooting

### Product Not Being Tracked
- **Check**: Is intent detected correctly? (needs to be product-related)
- **Check**: Is `track_product_mention()` being called?
- **Check**: Product name matches catalog keywords?

### Always Getting "ask" Status
- **Check**: Clear previous context? `clear_product_context(session_id)`
- **Check**: No products mentioned recently (TTL might have expired)?

### Multiple Products Show "direct"
- **Check**: Resolve returns "choose" if 2+ high-confidence candidates

### State Not Transitioning
- **Check**: Ensure `conversation_state` is passed in request correctly

## Performance Notes

- **Memory**: ~1KB per session (in-memory, default dict)
- **Lookup**: O(1) session lookup, O(n) candidate filtering (n ≤ 3)
- **TTL Check**: O(n) scan when getting context (n ≤ 3)
- **No DB calls** for product context (all in-memory)

## Future Extensibility

To add product categories:
```python
# Enhanced product tracking with category
add_product_candidate(
    session_id,
    product_name,
    source,
    confidence,
    category="shoes"  # Future parameter
)
```

To persist to MongoDB:
```python
# Replace in-memory store with MongoDB collection
db["product_context"].update_one(
    {"session_id": session_id},
    {"$set": {...}},
    upsert=True
)
```

---

**Version**: 1.0  
**Last Updated**: April 17, 2026  
