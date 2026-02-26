# app/models/intent_classifier.py

# =====================================================
# CB-3: Enhanced Intent Classification with synonym support
# =====================================================

INTENT_KEYWORDS = {
    "pricing": [
        "prix", "coûte", "coute", "tarif", "combien", "€", "TND", "DT",
        "price", "cost", "how much", "montant", "valeur", "cher"
    ],
    "services": [
        "livraison", "delivery", "retour", "return", "shipping",
        "expédition", "délai", "frais", "envoyer", "recevoir",
        "send", "ship", "arrive", "quand", "when"
    ],
    "orders": [
        "commande", "order", "acheter", "buy", "purchase",
        "achat", "commander", "réserver", "payer", "payment",
        "panier", "cart", "checkout"
    ],
    "catalog": [
        "liste", "produits", "catalogue", "tous", "disponible",
        "catalog", "products", "available", "stock", "collection",
        "quels produits", "what products", "montrer", "show",
        "voir", "see", "afficher", "display"
    ],
    "product_info": [
        "caractéristique", "spécification", "détail", "description",
        "features", "specs", "taille", "couleur", "matériel",
        "info", "information", "describe", "what is", "c'est quoi"
    ],
    "support": [
        "aide", "help", "problème", "issue", "contact", "support",
        "service client", "customer service", "réclamation", "complaint",
        "agent", "humain", "human", "parler", "speak"
    ]
}


def classify_intent(query: str) -> str:
    """
    Enhanced intent classification using keyword matching with scoring
    
    CB-3: Intent Classification Model
    
    Args:
        query: User question
        
    Returns:
        Intent category: pricing, services, orders, catalog, product_info, support, other
    """
    query_lower = query.lower()
    
    # Count matches for each intent
    intent_scores = {}
    
    for intent, keywords in INTENT_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in query_lower)
        if score > 0:
            intent_scores[intent] = score
    
    # Return intent with highest score
    if intent_scores:
        return max(intent_scores, key=intent_scores.get)
    
    # Default fallback
    return "product_info"


def is_listing_request(query: str) -> bool:
    """
    Detect if user wants a list of products
    
    CB-3: Intent Classification Model
    
    Args:
        query: User question
        
    Returns:
        True if it's a catalog/listing request
    """
    q = query.lower()
    
    listing_keywords = [
        "tous les produits", "liste des produits", "catalogue", 
        "produits disponibles", "all products", "product list",
        "montrer tous", "show all", "quels produits", "what products",
        "voir les produits", "see products", "afficher produits",
        "quels sont", "what are", "liste", "list"
    ]
    
    return any(keyword in q for keyword in listing_keywords)


def get_intent_confidence(query: str, detected_intent: str) -> float:
    """
    Calculate confidence score for detected intent
    
    CB-3: Intent Classification Model
    
    Args:
        query: User question
        detected_intent: Detected intent category
        
    Returns:
        Confidence score between 0 and 1
    """
    query_lower = query.lower()
    
    if detected_intent not in INTENT_KEYWORDS:
        return 0.3  # Low confidence for unknown intent
    
    keywords = INTENT_KEYWORDS[detected_intent]
    matches = sum(1 for keyword in keywords if keyword in query_lower)
    
    # Calculate confidence based on number of matches
    if matches >= 3:
        return 0.95
    elif matches == 2:
        return 0.80
    elif matches == 1:
        return 0.60
    else:
        return 0.30