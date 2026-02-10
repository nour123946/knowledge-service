def classify_intent(query: str):
    query = query.lower()

    if any(word in query for word in ["prix", "coute", "price", "tarif"]):
        return "pricing"

    if any(word in query for word in ["livraison", "delivery", "retour", "service"]):
        return "services"

    if any(word in query for word in ["commande", "order", "acheter"]):
        return "orders"

    return "product_info"
def is_listing_request(query: str) -> bool:
    q = query.lower()
    keywords = ["tous les produits", "liste des produits", "catalogue", "produits disponibles"]
    return any(k in q for k in keywords)
