# app/core/entities.py

import re


def extract_entities(user_text: str, retrieved_chunks: list = None) -> dict:
    """
    Smart entity extraction.
    Works dynamically with ANY data.
    """

    entities = {}
    text_lower = user_text.lower()

    # -------------------------
    # 📧 Email
    # -------------------------
    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', user_text)
    if email_match:
        entities["email"] = email_match.group()

    # -------------------------
    # 🧾 Order ID (generic pattern)
    # -------------------------
    order_match = re.search(r'\b[A-Z]{2,}-\d+\b', user_text)
    if order_match:
        entities["order_id"] = order_match.group()

    # -------------------------
    # 🔢 Product reference (first, second...)
    # -------------------------
    if "premier" in text_lower or "first" in text_lower:
        entities["product_reference"] = 1
    elif "deuxième" in text_lower or "second" in text_lower:
        entities["product_reference"] = 2
    elif "troisième" in text_lower or "third" in text_lower:
        entities["product_reference"] = 3

    # -------------------------
    # 🛍 Product name detection from knowledge base (DYNAMIC)
    # -------------------------
    if retrieved_chunks:
        flat_chunks = []
        for item in retrieved_chunks:
            if isinstance(item, list):
                flat_chunks.extend(item)
            else:
                flat_chunks.append(item)

        context_text = " ".join(flat_chunks).lower()

        # find capitalized word sequences from chunks
        possible_products = re.findall(r'\b[A-Z][a-zA-Z0-9]+\b(?:\s+[A-Z][a-zA-Z0-9]+)*', " ".join(flat_chunks))

        for product in possible_products:
            if product.lower() in text_lower:
                entities["product_name"] = product
                break

    # -------------------------
    # 🚚 Delivery intent
    # -------------------------
    if "livraison" in text_lower or "delivery" in text_lower:
        entities["intent_type"] = "delivery"

    # -------------------------
    # 🔁 Return intent
    # -------------------------
    if "retour" in text_lower or "return" in text_lower:
        entities["intent_type"] = "return"

    return entities
