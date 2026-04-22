"""
Gestionnaire de commandes
Crée et enregistre les commandes dans MongoDB
"""

from typing import Dict, Optional
from datetime import datetime
from app.core.database import get_database
from app.core.cart_manager import CartManager

# Frais de livraison
DELIVERY_FEE = 8  # TND


class OrderManager:
    """Gestion des commandes clients"""
    
    def __init__(self):
        self.db = get_database()
        self.orders_collection = self.db.orders
        
        # Créer index sur order_id pour performance
        self.orders_collection.create_index("order_id", unique=True)
        self.orders_collection.create_index("session_id")
        self.orders_collection.create_index("status")
        self.orders_collection.create_index("created_at")
    
    def generate_order_id(self) -> str:
        """
        Génère un ID unique pour la commande
        Format: CMD-YYYYMMDD-XXX
        """
        today = datetime.utcnow().strftime("%Y%m%d")
        
        # Compter les commandes du jour
        count = self.orders_collection.count_documents({
            "order_id": {"$regex": f"^CMD-{today}-"}
        })
        
        order_number = count + 1
        return f"CMD-{today}-{order_number:03d}"
    
    def create_order(
        self,
        session_id: str,
        customer_info: Dict,
        cart_items: list,
        payment_method: str,
        channel: str = "web"
    ) -> Dict:
        """
        Crée une nouvelle commande
        
        Args:
            session_id: ID de session du client
            customer_info: {
                "name": str,
                "phone": str,
                "address": str
            }
            cart_items: Liste des produits du panier
            payment_method: "cash_on_delivery" ou "card"
            channel: "web", "whatsapp", "facebook"
        
        Returns:
            Dictionnaire de la commande créée
        """
        
        # Calculer les montants
        subtotal = sum(
            item["price"] * item["quantity"] 
            for item in cart_items
        )
        total = subtotal + DELIVERY_FEE
        
        # Générer ID de commande
        order_id = self.generate_order_id()
        
        # Créer la commande avec status_history
        now = datetime.utcnow()
        order = {
            "order_id": order_id,
            "session_id": session_id,
            "customer": {
                "name": customer_info.get("name", ""),
                "phone": customer_info.get("phone", ""),
                "address": customer_info.get("address", "")
            },
            "items": cart_items,
            "subtotal": subtotal,
            "delivery_fee": DELIVERY_FEE,
            "total": total,
            "payment_method": payment_method,
            "status": "pending",  # pending, confirmed, shipped, delivered, cancelled
            "tracking_number": None,
            "channel": channel,
            "status_history": [{
                "status": "pending",
                "changed_at": now,
                "changed_by": "system",
                "note": "Order created"
            }],
            "created_at": now,
            "updated_at": now
        }
        
        # Insérer dans MongoDB
        result = self.orders_collection.insert_one(order)
        order["_id"] = result.inserted_id
        
        print(f"✅ Commande {order_id} créée avec succès")
        
        return order
    
    def get_order(self, order_id: str) -> Optional[Dict]:
        """Récupère une commande par son ID"""
        return self.orders_collection.find_one({"order_id": order_id})
    
    def get_orders_by_session(self, session_id: str) -> list:
        """Récupère toutes les commandes d'une session"""
        return list(self.orders_collection.find({"session_id": session_id}))
    
    def update_order_status(self, order_id: str, new_status: str, note: str = "", changed_by: str = "admin") -> bool:
        """
        Met à jour le statut d'une commande et ajoute à l'historique
        
        Status possibles: pending, confirmed, shipped, delivered, cancelled
        
        Args:
            order_id: Order ID
            new_status: New status
            note: Optional note explaining the change
            changed_by: Who made the change (admin, user, system)
        """
        now = datetime.utcnow()
        
        result = self.orders_collection.update_one(
            {"order_id": order_id},
            {
                "$set": {
                    "status": new_status,
                    "updated_at": now
                },
                "$push": {
                    "status_history": {
                        "status": new_status,
                        "changed_at": now,
                        "changed_by": changed_by,
                        "note": note
                    }
                }
            }
        )
        
        return result.modified_count > 0
    
    def cancel_order(self, order_id: str) -> bool:
        """Annule une commande"""
        return self.update_order_status(order_id, "cancelled", note="Order canceled", changed_by="admin")
    
    def update_tracking_number(self, order_id: str, tracking_number: str, note: str = "Tracking number set") -> bool:
        """Met à jour le numéro de suivi"""
        result = self.orders_collection.update_one(
            {"order_id": order_id},
            {
                "$set": {
                    "tracking_number": tracking_number,
                    "updated_at": datetime.utcnow()
                }
            }
        )
        return result.modified_count > 0
    
    def get_all_orders(self, status: Optional[str] = None) -> list:
        """
        Récupère toutes les commandes (pour le dashboard)
        
        Args:
            status: Filtrer par statut (optionnel)
        """
        query = {}
        if status:
            query["status"] = status
        
        return list(self.orders_collection.find(query).sort("created_at", -1))
    
    def get_pending_orders(self) -> list:
        """Récupère les commandes en attente"""
        return self.get_all_orders(status="pending")
    
    def format_order_summary(self, order: Dict) -> str:
        """
        Formate un récapitulatif de commande pour affichage
        
        Returns:
            Texte formaté
        """
        payment_methods = {
            "cash_on_delivery": "💵 Paiement à la livraison",
            "card": "💳 Paiement par carte"
        }
        
        text = f"""
✅ **RÉCAPITULATIF DE VOTRE COMMANDE**

📝 Numéro de commande : **{order['order_id']}**

👤 **Informations client :**
• Nom : {order['customer']['name']}
• Téléphone : {order['customer']['phone']}
• Adresse : {order['customer']['address']}

📦 **Votre commande :**
"""
        
        for item in order['items']:
            text += f"\n• {item['product_name']}"
            text += f"\n  {item['price']:.0f} TND × {item['quantity']} = {item['price'] * item['quantity']:.0f} TND"
            text += f"\n  🚚 Livraison : {item.get('delivery_time', 'Non spécifié')}"
        
        text += f"\n\n{'─' * 40}"
        text += f"\n💰 Sous-total : {order['subtotal']:.0f} TND"
        text += f"\n🚚 Livraison : {order['delivery_fee']:.0f} TND"
        text += f"\n{'─' * 40}"
        text += f"\n💳 **TOTAL À PAYER : {order['total']:.0f} TND**"
        text += f"\n\n💰 Mode de paiement : {payment_methods.get(order['payment_method'], order['payment_method'])}"
        
        return text.strip()


def get_order_manager() -> OrderManager:
    """Factory pour créer un gestionnaire de commandes"""
    return OrderManager()


# Test du gestionnaire
if __name__ == "__main__":
    from app.core.cart_manager import CartManager
    
    # Simuler une commande
    test_session = "test_order_123"
    
    # 1. Créer un panier
    print("📦 Création du panier...")
    cart_mgr = CartManager(test_session)
    
    test_product = {
        "name": "Puma RS-X",
        "price": 310,
        "delivery_time": "72h",
        "in_stock": True
    }
    
    cart_mgr.add_item(test_product, quantity=1)
    cart_summary = cart_mgr.get_cart_summary()
    
    # 2. Créer la commande
    print("\n📝 Création de la commande...")
    order_mgr = OrderManager()
    
    customer_info = {
        "name": "Ahmed Benali",
        "phone": "55123456",
        "address": "Avenue Habib Bourguiba, Tunis"
    }
    
    order = order_mgr.create_order(
        session_id=test_session,
        customer_info=customer_info,
        cart_items=cart_summary["items"],
        payment_method="cash_on_delivery",
        channel="web"
    )
    
    # 3. Afficher le récapitulatif
    print("\n" + order_mgr.format_order_summary(order))
    
    # 4. Marquer le panier comme converti
    cart_mgr.mark_as_converted()
    
    print("\n✅ Test terminé avec succès !")
    print(f"📝 Commande créée : {order['order_id']}")