"""
Workflow de commande intelligent
Gère les états de conversation et le flux de commande complet
"""

from typing import Dict, Optional, Tuple
from datetime import datetime
from app.core.cart_manager import CartManager
from app.core.order_manager import OrderManager
from app.core.database import get_database
from app.core.memory import get_history  # 🔥 AJOUTÉ
from app.utils.product_parser import (
    parse_business_data, 
    get_product_by_name, 
    format_product_info,
    get_available_products,
    get_out_of_stock_products
)
import logging

logger = logging.getLogger(__name__)

# États de conversation possibles
class STATES:
    """Constantes pour les états de conversation"""
    IDLE = "idle"
    BROWSING = "browsing"
    ADDING_TO_CART = "adding_to_cart"
    WAITING_NAME = "collecting_name"
    WAITING_PHONE = "collecting_phone"
    WAITING_ADDRESS = "collecting_address"
    WAITING_PAYMENT = "collecting_payment"
    CONFIRM = "confirming_order"
    CONFIRMED = "order_placed"


class OrderWorkflow:
    """Gère le flux conversationnel de commande"""
    
    def __init__(self, session_id: str, channel: str = "web"):
        self.session_id = session_id
        self.channel = channel
        self.cart_manager = CartManager(session_id)
        self.order_manager = OrderManager()
        
        # Charger les produits
        self.products = parse_business_data()
        
        # État de la conversation
        self.state = STATES.IDLE
        
        # Charger les données temporaires depuis MONGODB
        self.db = get_database()
        self.temp_collection = self.db["order_temp_data"]
        
        # Récupérer les données temporaires existantes
        saved_data = self.temp_collection.find_one({"session_id": session_id})
        if saved_data:
            self.temp_data = saved_data.get("data", {})
        else:
            self.temp_data = {}
    
    def _save_temp_data(self):
        """Sauvegarde les données temporaires en base"""
        self.temp_collection.update_one(
            {"session_id": self.session_id},
            {"$set": {"data": self.temp_data, "updated_at": datetime.utcnow()}},
            upsert=True
        )
    
    def handle_user_message(
        self, 
        user_message: str, 
        intent: str,
        current_state: str = None
    ) -> Tuple[str, str]:
        """
        Gère le message utilisateur selon l'état actuel
        
        Args:
            user_message: Message de l'utilisateur
            intent: Intention détectée
            current_state: État actuel de la conversation
        
        Returns:
            (response, new_state): Réponse du bot et nouvel état
        """
        
        if current_state and current_state != "idle":
            self.state = current_state
        
        user_message_lower = user_message.lower()
        
        logger.info(f"📦 WORKFLOW: state={self.state}, message={user_message}")
        
        # 🔥 CAS SPÉCIAL : "oui" après proposition de produit
        if user_message_lower.strip() in ["oui", "ok", "d'accord", "parfait"] and self.state == STATES.IDLE:
            # Récupérer le dernier message du bot
            history = get_history(self.session_id, last_n=3)
            last_bot_message = ""
            for msg in reversed(history):
                if msg['role'] == 'assistant':
                    last_bot_message = msg['content'].lower()
                    break
            
            # Détecter le produit mentionné
            products_map = {
                "puma": {"name": "Puma RS-X", "price": 310, "in_stock": True},
                "adidas": {"name": "Adidas Ultraboost", "price": 420, "in_stock": True},
                "converse": {"name": "Converse Chuck Taylor", "price": 190, "in_stock": True},
                "new balance": {"name": "New Balance 574", "price": 260, "in_stock": True}
            }
            
            detected_product = None
            for key, product in products_map.items():
                if key in last_bot_message:
                    detected_product = product
                    break
            
            if detected_product:
                # Ajouter au panier
                self.cart_manager.add_item(detected_product, quantity=1)
                
                # Passer directement à la collecte du nom
                response = f"✅ **{detected_product['name']}** ajouté au panier ({detected_product['price']} TND).\n\n"
                response += "Pour finaliser, quel est votre nom complet ? 👤"
                
                return response, STATES.WAITING_NAME
        
        # ======= GESTION DES ANNULATIONS =======
        if any(word in user_message_lower for word in ["annuler", "annule", "stop", "non merci"]):
            if self.state != STATES.IDLE:
                self.cart_manager.clear_cart()
                self.temp_data = {}
                self._save_temp_data()
                self.state = STATES.IDLE
                return "❌ Commande annulée. Comment puis-je vous aider ?", STATES.IDLE
        
        # ======= FLUX DE COLLECTE D'INFORMATIONS =======
        
        # Collecte du nom
        if self.state == STATES.WAITING_NAME:
            self.temp_data["name"] = user_message.strip()
            self._save_temp_data()
            return "Merci ! Quel est votre numéro de téléphone ? 📞", STATES.WAITING_PHONE
        
        # Collecte du téléphone
        elif self.state == STATES.WAITING_PHONE:
            phone = user_message.strip().replace(" ", "")
            if len(phone) >= 8 and phone.isdigit():
                self.temp_data["phone"] = phone
                self._save_temp_data()
                return "Parfait ! Quelle est votre adresse de livraison complète ? 📍", STATES.WAITING_ADDRESS
            else:
                return "⚠️ Le numéro de téléphone doit contenir au moins 8 chiffres. Réessayez :", STATES.WAITING_PHONE
        
        # Collecte de l'adresse
        elif self.state == STATES.WAITING_ADDRESS:
            self.temp_data["address"] = user_message.strip()
            self._save_temp_data()
            return (
                "Merci ! Comment souhaitez-vous payer ? 💳\n\n"
                "1️⃣ Paiement à la livraison (cash)\n"
                "2️⃣ Paiement par carte bancaire\n\n"
                "Tapez **1** ou **2**"
            ), STATES.WAITING_PAYMENT
        
        # Collecte du mode de paiement
        elif self.state == STATES.WAITING_PAYMENT:
            if "1" in user_message or "livraison" in user_message_lower or "cash" in user_message_lower:
                self.temp_data["payment_method"] = "cash_on_delivery"
                self._save_temp_data()
            elif "2" in user_message or "carte" in user_message_lower:
                self.temp_data["payment_method"] = "card"
                self._save_temp_data()
            else:
                return "⚠️ Réponse non valide. Tapez **1** pour paiement à la livraison ou **2** pour carte :", STATES.WAITING_PAYMENT
            
            return self._show_order_confirmation()
        
        # Confirmation finale
        elif self.state == STATES.CONFIRM:
            if any(word in user_message_lower for word in ["oui", "confirme", "ok", "valider", "d'accord"]):
                return self._finalize_order()
            elif any(word in user_message_lower for word in ["non", "annuler"]):
                self.cart_manager.mark_as_abandoned()
                self.temp_data = {}
                self._save_temp_data()
                return "❌ Commande annulée. Puis-je vous aider avec autre chose ?", STATES.IDLE
            else:
                return "⚠️ Veuillez répondre par **Oui** pour confirmer ou **Non** pour annuler.", STATES.CONFIRM
        
        # ======= GESTION DES INTENTIONS =======
        
        # Finaliser la commande
        if any(word in user_message_lower for word in ["finaliser", "finalise", "commander", "passer commande", "acheter", "valider commande"]):
            if self.cart_manager.is_empty():
                return "🛒 Votre panier est vide. Ajoutez d'abord des produits !", STATES.IDLE
            else:
                return "Parfait ! Pour finaliser votre commande, quel est votre nom complet ? 👤", STATES.WAITING_NAME
        
        # Voir le panier
        if intent == "cart_view" or "panier" in user_message_lower:
            return self._show_cart(), STATES.BROWSING
        
        # Demande d'info produit
        if intent in ["product_info", "product_price", "product_availability"]:
            return self._handle_product_inquiry(user_message)
        
        # Ajout au panier
        if intent in ["cart_add", "order_create", "orders"]:
            return self._handle_add_to_cart(user_message)
        
        # Par défaut
        return "Je peux vous aider à passer commande. Quel produit vous intéresse ? 👟", STATES.IDLE
    
    # ... (reste des méthodes identique)
    
    def _show_order_confirmation(self) -> Tuple[str, str]:
        """Affiche le récapitulatif final avant confirmation"""
        
        cart_summary = self.cart_manager.get_cart_summary()
        
        payment_labels = {
            "cash_on_delivery": "💵 Paiement à la livraison",
            "card": "💳 Paiement par carte bancaire"
        }
        
        response = "✅ **RÉCAPITULATIF DE VOTRE COMMANDE**\n\n"
        response += f"👤 **Nom :** {self.temp_data.get('name', 'Non fourni')}\n"
        response += f"📞 **Téléphone :** {self.temp_data.get('phone', 'Non fourni')}\n"
        response += f"📍 **Adresse :** {self.temp_data.get('address', 'Non fourni')}\n\n"
        response += "📦 **Commande :**\n"
        
        for item in cart_summary["items"]:
            response += f"• {item['product_name']} - {item['price']:.0f} TND × {item['quantity']}\n"
        
        response += f"\n{'─' * 40}\n"
        response += f"💰 Sous-total : {cart_summary['subtotal']:.0f} TND\n"
        response += f"🚚 Livraison : {cart_summary['delivery_fee']:.0f} TND\n"
        response += f"{'─' * 40}\n"
        response += f"💳 **TOTAL : {cart_summary['total']:.0f} TND**\n\n"
        response += f"💰 {payment_labels.get(self.temp_data.get('payment_method'), 'Paiement')}\n\n"
        response += "**Confirmez-vous cette commande ? (Oui/Non)**"
        
        return response, STATES.CONFIRM
    
    def _finalize_order(self) -> Tuple[str, str]:
        """Finalise et enregistre la commande"""
        
        cart_summary = self.cart_manager.get_cart_summary()
        
        order = self.order_manager.create_order(
            session_id=self.session_id,
            customer_info={
                "name": self.temp_data.get("name", "Inconnu"),
                "phone": self.temp_data.get("phone", "Non fourni"),
                "address": self.temp_data.get("address", "Non fourni")
            },
            cart_items=cart_summary["items"],
            payment_method=self.temp_data.get("payment_method", "cash_on_delivery"),
            channel=self.channel
        )
        
        self.cart_manager.mark_as_converted()
        self.temp_data = {}
        self._save_temp_data()
        
        response = f"""
🎉 **COMMANDE CONFIRMÉE !**

📝 Numéro de commande : **{order['order_id']}**

Notre équipe vous contactera dans les **24 heures** pour :
✅ Confirmer la disponibilité
✅ Organiser la livraison

Merci de votre confiance ! 😊
"""
        
        return response.strip(), STATES.CONFIRMED
    
   


# Test du workflow
if __name__ == "__main__":
    print("🧪 Test du workflow de commande\n")
    
    workflow = OrderWorkflow("test_workflow_session", channel="web")
    
    # Simulation de conversation
    messages = [
        ("Je veux acheter des Puma RS-X", "order_create"),
        ("finaliser", "order_create"),
        ("Ahmed Benali", "provide_info"),
        ("55123456", "provide_info"),
        ("Avenue Bourguiba, Tunis", "provide_info"),
        ("1", "provide_info"),
        ("Oui", "order_confirm")
    ]
    
    state = STATES["IDLE"]
    
    for msg, intent in messages:
        print(f"\n👤 Client : {msg}")
        response, state = workflow.handle_user_message(msg, intent, state)
        print(f"🤖 Bot : {response}")
        print(f"📊 État : {state}")
        print("-" * 60)