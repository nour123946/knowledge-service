"""
Gestionnaire de panier pour le chatbot
Permet d'ajouter/retirer des produits avant de passer commande
"""

from typing import Dict, List, Optional
from datetime import datetime
from app.core.database import get_database

DELIVERY_FEE = 8  # Frais de livraison fixes en TND


class CartManager:
    """Gestion du panier client"""
    
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.db = get_database()
        self.carts_collection = self.db.carts
    
    def get_or_create_cart(self) -> Dict:
        """Récupère le panier actif ou en crée un nouveau"""
        cart = self.carts_collection.find_one({
            "session_id": self.session_id,
            "status": "active"
        })
        
        if not cart:
            cart = {
                "session_id": self.session_id,
                "items": [],
                "status": "active",
                "created_at": datetime.utcnow(),
                "updated_at": datetime.utcnow()
            }
            result = self.carts_collection.insert_one(cart)
            cart["_id"] = result.inserted_id
        
        return cart
    
    def add_item(self, product: Dict, quantity: int = 1) -> Dict:
        """
        Ajoute un produit au panier
        
        Args:
            product: Dictionnaire du produit (depuis product_parser)
            quantity: Quantité à ajouter
        
        Returns:
            Panier mis à jour
        """
        cart = self.get_or_create_cart()
        
        # Vérifier si le produit existe déjà dans le panier
        item_exists = False
        for item in cart["items"]:
            if item["product_name"] == product["name"]:
                item["quantity"] += quantity
                item_exists = True
                break
        
        # Sinon, ajouter nouveau produit
        if not item_exists:
            cart["items"].append({
                "product_name": product["name"],
                "price": product["price"],
                "quantity": quantity,
                "delivery_time": product.get("delivery_time", "Non spécifié")
            })
        
        # Mettre à jour dans MongoDB
        self.carts_collection.update_one(
            {"_id": cart["_id"]},
            {
                "$set": {
                    "items": cart["items"],
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        return self.get_or_create_cart()
    
    def remove_item(self, product_name: str) -> Dict:
        """
        Retire un produit du panier
        
        Args:
            product_name: Nom du produit à retirer
        
        Returns:
            Panier mis à jour
        """
        cart = self.get_or_create_cart()
        
        cart["items"] = [
            item for item in cart["items"] 
            if item["product_name"].lower() != product_name.lower()
        ]
        
        self.carts_collection.update_one(
            {"_id": cart["_id"]},
            {
                "$set": {
                    "items": cart["items"],
                    "updated_at": datetime.utcnow()
                }
            }
        )
        
        return self.get_or_create_cart()
    
    def clear_cart(self):
        """Vide complètement le panier"""
        cart = self.get_or_create_cart()
        
        self.carts_collection.update_one(
            {"_id": cart["_id"]},
            {
                "$set": {
                    "items": [],
                    "updated_at": datetime.utcnow()
                }
            }
        )
    
    def get_cart_summary(self) -> Dict:
        """
        Retourne un résumé du panier avec calculs
        
        Returns:
            {
                "items": [...],
                "subtotal": float,
                "delivery_fee": float,
                "total": float,
                "item_count": int
            }
        """
        cart = self.get_or_create_cart()
        
        subtotal = sum(
            item["price"] * item["quantity"] 
            for item in cart["items"]
        )
        
        total = subtotal + DELIVERY_FEE
        item_count = sum(item["quantity"] for item in cart["items"])
        
        return {
            "items": cart["items"],
            "subtotal": subtotal,
            "delivery_fee": DELIVERY_FEE,
            "total": total,
            "item_count": item_count
        }
    
    def format_cart_text(self) -> str:
        """
        Formate le panier pour affichage dans le chatbot
        
        Returns:
            Texte formaté du panier
        """
        summary = self.get_cart_summary()
        
        if not summary["items"]:
            return "🛒 Votre panier est vide."
        
        text = "📦 **VOTRE PANIER**\n\n"
        
        for item in summary["items"]:
            text += f"• {item['product_name']}\n"
            text += f"  {item['price']:.0f} TND × {item['quantity']} = {item['price'] * item['quantity']:.0f} TND\n"
            text += f"  🚚 Livraison : {item['delivery_time']}\n\n"
        
        text += "─" * 40 + "\n"
        text += f"💰 Sous-total : {summary['subtotal']:.0f} TND\n"
        text += f"🚚 Livraison : {summary['delivery_fee']:.0f} TND\n"
        text += "─" * 40 + "\n"
        text += f"💳 **TOTAL : {summary['total']:.0f} TND**\n"
        
        return text
    
    def is_empty(self) -> bool:
        """Vérifie si le panier est vide"""
        cart = self.get_or_create_cart()
        return len(cart["items"]) == 0
    
    def mark_as_converted(self):
        """Marque le panier comme converti en commande"""
        cart = self.get_or_create_cart()
        
        self.carts_collection.update_one(
            {"_id": cart["_id"]},
            {
                "$set": {
                    "status": "converted",
                    "updated_at": datetime.utcnow()
                }
            }
        )
    
    def mark_as_abandoned(self):
        """Marque le panier comme abandonné"""
        cart = self.get_or_create_cart()
        
        self.carts_collection.update_one(
            {"_id": cart["_id"]},
            {
                "$set": {
                    "status": "abandoned",
                    "updated_at": datetime.utcnow()
                }
            }
        )


# Fonctions utilitaires
def get_cart_manager(session_id: str) -> CartManager:
    """Factory pour créer un gestionnaire de panier"""
    return CartManager(session_id)


# Test du gestionnaire
if __name__ == "__main__":
    # Test avec un panier fictif
    cart_mgr = CartManager("test_session_123")
    
    # Simuler des produits
    test_product_1 = {
        "name": "Puma RS-X",
        "price": 310,
        "delivery_time": "72h",
        "in_stock": True
    }
    
    test_product_2 = {
        "name": "Converse Chuck Taylor",
        "price": 190,
        "delivery_time": "48h",
        "in_stock": True
    }
    
    # Ajouter des produits
    print("➕ Ajout Puma RS-X...")
    cart_mgr.add_item(test_product_1, quantity=1)
    
    print("➕ Ajout Converse Chuck Taylor...")
    cart_mgr.add_item(test_product_2, quantity=2)
    
    # Afficher le panier
    print("\n" + cart_mgr.format_cart_text())
    
    # Retirer un produit
    print("\n➖ Retrait Puma RS-X...")
    cart_mgr.remove_item("Puma RS-X")
    
    print("\n" + cart_mgr.format_cart_text())
    
    # Vider le panier
    print("\n🗑️ Vidage du panier...")
    cart_mgr.clear_cart()
    
    print("\n" + cart_mgr.format_cart_text())