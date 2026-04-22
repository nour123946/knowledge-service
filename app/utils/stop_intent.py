"""
Utility function for detecting user's intent to stop/close conversation or SAV flow.
"""


def is_stop_intent(text: str) -> bool:
    """
    Détecte si l'utilisateur veut quitter (stop/close intent).
    TRUE si le texte contient des marqueurs de fermeture.
    FALSE si le texte contient une nouvelle demande explicite.
    
    Marqueurs: "non merci", "merci", "ça va", "c'est bon", "rien", "aucune demande",
               "je veux rien", "pas besoin", "stop", "annule", "quit", "bye", "au revoir"
    """
    t = (text or "").lower().strip()
    
    # Marqueurs de fermeture
    stop_markers = [
        "non merci", "ok merci", "ça va", "c'est bon", "rien", "aucune demande",
        "je veux rien", "je voudrais rien", "pas besoin", "stop", "quit", "bye", 
        "au revoir", "au revoir merci", "merci c'est tout", "c'est bon merci"
    ]
    
    if not any(m in t for m in stop_markers):
        return False
    
    # Mais vérifier s'il y a une nouvelle action explicite (override)
    action_keywords = [
        "je veux annuler ma commande", "annuler ma commande",
        "je veux changer l'adresse", "changer l'adresse",
        "je veux échanger", "je veux retourner", "échanger", "retourner",
        "je veux avoir un remboursement", "remboursement",
        "aide-moi", "aidez-moi", "j'ai besoin", "problème", "bug"
    ]
    
    if any(kw in t for kw in action_keywords):
        return False  # There's a new request, don't close
    
    return True
