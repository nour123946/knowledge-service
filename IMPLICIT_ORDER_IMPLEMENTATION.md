## Implicit Order Handling - Product Context Memory Implementation

### Overview
This feature enables the chatbot to remember products discussed in a conversation and intelligently add them to cart when the user says "je veux commander" without specifying which product, eliminating ambiguity and enhancing user experience.

### Problem Addressed
**Before Implementation:**
- User: "avez vous adidas ?" → Bot provides product info
- User: "je veux commander" → Bot: "Votre panier est vide. Ajoutez d'abord des produits !"
- User has to repeat product name: "je veux commander adidas"

**After Implementation:**
- User: "avez vous adidas ?" → Bot provides product info + tracks context
- User: "je veux commander" → Bot automatically adds Adidas to cart
- User proceeds directly to checkout (collecting name)

### Architecture & Components

#### 1. Product Context Memory (app/core/memory.py)
**NEW CONTENT:**
- `PRODUCT_CONTEXT_STORE`: Persistent in-memory store per session
  ```
  {
    session_id: {
      "last_candidates": [
        {
          "name": "Adidas Ultraboost",
          "source": "catalog|rag|query|response|user_selection",
          "confidence": 0.88,
          "ts": datetime
        }
      ],
      "selected_product": "Adidas Ultraboost" | None,
      "last_updated_at": datetime
    }
  }
  ```

**New Functions:**
- `add_product_candidate(session_id, product_name, source, confidence)`: Add candidate (deduplicates by name, keeps 3 max)
- `get_product_context(session_id)`: Get candidates + selected product (removes expired >10 min)
- `set_product_selection(session_id, product_name)`: Mark as explicitly selected
- `clear_product_context(session_id)`: Clear context (after order placed)

#### 2. Product Resolution Logic (app/main.py)
**NEW FUNCTION: resolve_product_for_order(query, session_id)**

Determines what product to add when user says "commander" without specifying which:

**Resolution Rules (priority order):**
1. **DIRECT (Explicit)**: Query contains unique product name
   - "je veux commander converse" → Converse Chuck Taylor (confidence 0.95)
   - "je veux commander adidas ultraboost" → Direct match

2. **DIRECT (Selected)**: User previously selected one
   - Context has `selected_product` still fresh → Use it (confidence 0.92)

3. **DIRECT (Context Single)**: Single high-confidence candidate
   - Product discussed, exactly 1 option → Add it (confidence depends on candidate)
   - "quel est le prix du puma ?" + "je veux commander" → Puma RS-X

4. **CHOOSE**: Multiple high-confidence candidates (confidence >= 0.75)
   - "quel est le prix du puma ?" + "et le prix du converse ?" + "je veux commander"
   - Response: Menu list (1. Puma RS-X, 2. Converse Chuck Taylor)

5. **ASK**: No candidates or low confidence
   - New session, no prior discussion → Ask "Quel produit voulez-vous commander ?"

**Return Value:**
```python
{
  "status": "direct" | "choose" | "ask",
  "product_name": str (if direct),
  "options": List[str] (if choose),
  "confidence": float
}
```

#### 3. Product Mention Tracking (app/main.py)
**NEW FUNCTION: track_product_mention(query, response, session_id, intent)**

Populates product_context whenever:
- User asks about product (price, availability, characteristics) → HIGH confidence (0.85)
- Bot mentions product in response to product query → HIGH confidence (0.88)
- Keywords: prix, coût, dispo, disponible, stock, couleur, taille, caractéristique

**Integration Points:**
- Called after RAG responses (line 1060 in HOWTO SAV section)
- Called after main RAG pipeline (line 1337 in fallback RAG)

#### 4. New Conversation States (app/workflows/order_workflow.py)
```python
ASKING_PRODUCT = "asking_product"      # "Quel produit voulez-vous ?"
CHOOSING_PRODUCT = "choosing_product"  # "Lequel vous intéresse ?"
```

**State Flow:**
```
idle
  ├─ (explicit product) → collecting_name
  ├─ (single candidate) → collecting_name
  ├─ (multiple candidates) → choosing_product → collecting_name
  └─ (no candidates) → asking_product → collecting_name
```

#### 5. Product Choice Handlers (app/workflows/order_workflow.py)

**CHOOSING_PRODUCT State Handler:**
- Accept numeric input (1, 2, 3...) or product name search
- Validate choice against stored options
- Mark as selected in product_context
- Add to cart with confidence boost
- Auto-transition to collecting_name

**ASKING_PRODUCT State Handler:**
- Extract product name from user input
- Match against catalog using `get_product_by_name()`
- If match found → add to cart + collecting_name
- If no match → remain in asking_product, ask for clarification

#### 6. Implicit Order Integration (app/workflows/order_workflow.py)

**When user says "commander/acheter/finaliser":**
1. If cart empty → call `resolve_product_for_order()`
2. Based on status:
   - **direct**: Add to cart auto → move to collecting_name
   - **choose**: Store options → move to choosing_product
   - **ask**: State → asking_product

### Key Features

#### Non-Hallucination Guarantee
- Only products in catalog used (hardcoded list)
- Explicit brand mention without full product name uses catalog to find exact model
- If ambiguous (e.g., "adidas" with 1 adidas model) → still asks for confirmation via choice menu if multiple exist
- No inference beyond matched keywords

#### TTL (Time-To-Live) Management
- Candidates expire after 10 minutes
- Prevents stale product context from previous long conversations
- `clear_product_context()` called after order placed

#### Confidence Scoring
- Query mentions: 0.75-0.85
- Response mentions: 0.88
- Explicit product in current query: 0.95
- Previously selected (fresh): 0.92
- Candidates filtered at >= 0.75 for "direct" status

#### Session Isolation
- Product context unique per session_id
- No cross-session interference
- Stored in-memory (can be persisted to MongoDB if needed)

### Test Scenarios (All Passing ✅)

**Test 1: Product Context Preserved**
```
Q1: "avez vous adidas ?"
  → Product tracked: Adidas Ultraboost (confidence 0.85)
Q2: "je veux commander"
  → resolve_product_for_order() status=direct
  → AUTO ADDS: Adidas Ultraboost
  → Move to collecting_name
RESULT: ✅ Product added automatically
```

**Test 2: Multiple Products → Choice Menu**
```
Q1: "quel est le prix du puma ?"
Q2: "et adidas combien ça coûte ?"
  → Candidates: ['Puma RS-X', 'Adidas Ultraboost']
Q3: "je veux commander"
  → resolve_product_for_order() status=choose
  → Response: "Lequel vous intéresse ? 1. Puma 2. Adidas"
  → Move to choosing_product
RESULT: ✅ Menu presented, waiting for choice
```

**Test 3: Explicit Product in Query**
```
Q1: "je veux commander converse"
  → resolve_product_for_order() finds "converse" in query
  → status=direct (confidence 0.95)
  → AUTO ADD: Converse Chuck Taylor
  → Move to collecting_name
RESULT: ✅ Direct add without ambiguity
```

**Test 4: No Context → Ask**
```
Q1: "je veux commander"
  → product_context empty
  → resolve_product_for_order() status=ask
  → Response: "Quel produit voulez-vous commander ? Options: Puma..."
  → Move to asking_product
  → Wait for user to specify
RESULT: ✅ Proper question flow
```

### Files Modified

1. **app/core/memory.py**
   - Added PRODUCT_CONTEXT_STORE
   - Added 4 new functions (add_product_candidate, get_product_context, set_product_selection, clear_product_context)

2. **app/main.py**
   - Added resolve_product_for_order() (85 lines)
   - Added track_product_mention() (30 lines)
   - Added 2 calls to track_product_mention() in RAG handlers
   - No breaking changes to existing code

3. **app/workflows/order_workflow.py**
   - Added ASKING_PRODUCT and CHOOSING_PRODUCT states
   - Added product_options field to __init__
   - Added _save_product_options() method
   - Added handlers for both new states (~60 lines)
   - Updated commander intent handler to use resolve_product_for_order()
   - Updated _save_temp_data() to persist product_options

### Usage Examples

**Scenario 1: Browse then Buy**
```
User: "avez vous du puma ?"
Bot: "Oui, nous avons Puma RS-X pour 310 TND, livraison 72h."
      [internally tracking: Puma RS-X candidate @ 0.85 confidence]

User: "je veux commander"
Bot: "✅ Puma RS-X ajouté au panier. Quel est votre nom complet ?"
      [state: collecting_name, ready for checkout]
```

**Scenario 2: Compare then Decide**
```
User: "prix converse vs adidas ?"
Bot: "Converse 190 TND, Adidas 420 TND. Livraison 48h pour les deux."
      [tracking: both products]

User: "je veux la moins chère"
Bot: "📦 Lequel vous intéresse ?
      1. Converse Chuck Taylor
      2. Adidas Ultraboost"
      [state: choosing_product]

User: "1"
Bot: "✅ Converse Chuck Taylor ajouté. Nom complet ?"
```

**Scenario 3: Direct Specification**
```
User: "je veux commander new balance"
Bot: "✅ New Balance 574 ajouté. Nom complet ?"
      [no ambiguity, direct match]
```

### Notes on Reliability

**Why This Won't Hallucinate:**
- Product list hardcoded (not LLM-generated)
- Keyword matching only (prix, stock, etc.)
- Explicit user choice at each ambiguous point
- TTL prevents stale information
- No product inference beyond catalog

**Fallback Behavior:**
- If system can't decide → ask user to clarify
- Never auto-adds if uncertain
- User always has explicit choice point in choosing/asking states

### Future Enhancements (Optional)

1. Persist product_context to MongoDB for recovery after session restart
2. Track product view duration to enhance confidence scoring
3. A/B test confidence thresholds (currently 0.75)
4. Add product bundle detection ("puma ET converse" counts as 2 separate)
5. Contextual suggestions ("Since you asked about running shoes...")

