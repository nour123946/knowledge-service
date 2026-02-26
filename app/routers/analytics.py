from fastapi import APIRouter, Header, HTTPException
from datetime import datetime, timedelta
from typing import Optional
import random

router = APIRouter(prefix="/admin/analytics", tags=["Analytics"])

# ===== FONCTION HELPER POUR VÉRIFIER L'API KEY =====

def verify_admin_key(x_api_key: Optional[str] = Header(None)):
    """Vérifie l'API key admin"""
    ADMIN_API_KEY = "MY_SUPER_ADMIN_TOKEN_123"
    if x_api_key != ADMIN_API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return True


# ===== PRODUITS LES PLUS VENDUS =====

@router.get("/top-selling-products")
async def get_top_selling_products(
    limit: int = 5,
    x_api_key: Optional[str] = Header(None)
):
    """Récupère les top produits vendus"""
    verify_admin_key(x_api_key)
    
    # Données de démo réalistes
    demo_data = [
        {"product_name": "Laptop Dell XPS 15", "sales_count": 12, "total_revenue": 24000},
        {"product_name": "iPhone 15 Pro", "sales_count": 8, "total_revenue": 9600},
        {"product_name": "MacBook Air M3", "sales_count": 6, "total_revenue": 8400},
        {"product_name": "Samsung Galaxy S24", "sales_count": 5, "total_revenue": 4000},
        {"product_name": "iPad Pro 12.9", "sales_count": 4, "total_revenue": 4800}
    ]
    
    return {"top_products": demo_data[:limit]}


# ===== PRODUITS LES PLUS DEMANDÉS =====

@router.get("/most-asked-products")
async def get_most_asked_products(
    limit: int = 5,
    x_api_key: Optional[str] = Header(None)
):
    """Récupère les produits les plus questionnés"""
    verify_admin_key(x_api_key)
    
    demo_data = [
        {"product_name": "iPhone 15 Pro", "question_count": 45, "top_intent": "product_info"},
        {"product_name": "MacBook Air M3", "question_count": 38, "top_intent": "product_price"},
        {"product_name": "AirPods Pro 2", "question_count": 32, "top_intent": "product_availability"},
        {"product_name": "Samsung Galaxy S24", "question_count": 28, "top_intent": "product_info"},
        {"product_name": "Sony WH-1000XM5", "question_count": 24, "top_intent": "product_price"}
    ]
    
    return {"most_asked_products": demo_data[:limit]}


# ===== LEADS GÉNÉRÉS =====

@router.get("/leads")
async def get_leads_stats(x_api_key: Optional[str] = Header(None)):
    """Statistiques sur les leads"""
    verify_admin_key(x_api_key)
    
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    # Timeline des leads (7 derniers jours)
    leads_timeline = []
    for i in range(7):
        day = today - timedelta(days=6-i)
        count = random.randint(5, 25)
        leads_timeline.append({
            "date": day.strftime("%Y-%m-%d"),
            "count": count
        })
    
    total_leads = sum(d["count"] for d in leads_timeline) * 4
    
    return {
        "total_leads": total_leads,
        "leads_today": leads_timeline[-1]["count"],
        "conversion_rate": 18.5,
        "leads_by_source": {
            "web": 156,
            "whatsapp": 89,
            "facebook": 67
        },
        "leads_timeline": leads_timeline
    }


# ===== PROGRESSION DES VENTES =====

@router.get("/sales-progression")
async def get_sales_progression(
    days: int = 7,
    x_api_key: Optional[str] = Header(None)
):
    """Progression des ventes"""
    verify_admin_key(x_api_key)
    
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    sales_data = []
    
    for i in range(days):
        day = today - timedelta(days=days - i - 1)
        daily_sales = random.randint(3, 12)
        daily_revenue = daily_sales * random.randint(800, 2000)
        
        sales_data.append({
            "date": day.strftime("%Y-%m-%d"),
            "sales_count": daily_sales,
            "revenue": daily_revenue
        })
    
    total_sales = sum(d["sales_count"] for d in sales_data)
    total_revenue = sum(d["revenue"] for d in sales_data)
    
    return {
        "total_sales": total_sales,
        "total_revenue": total_revenue,
        "growth_rate": 12.5,
        "sales_timeline": sales_data,
        "average_order_value": round(total_revenue / total_sales, 2) if total_sales > 0 else 0
    }


# ===== QUESTIONS FRÉQUENTES =====

@router.get("/frequent-questions")
async def get_frequent_questions(
    limit: int = 10,
    x_api_key: Optional[str] = Header(None)
):
    """Questions les plus fréquentes"""
    verify_admin_key(x_api_key)
    
    demo_questions = [
        {"intent": "product_info", "count": 145, "example": "Quelles sont les caractéristiques du produit ?"},
        {"intent": "product_price", "count": 128, "example": "Quel est le prix ?"},
        {"intent": "product_availability", "count": 112, "example": "Est-ce disponible en stock ?"},
        {"intent": "delivery_time", "count": 98, "example": "Délai de livraison ?"},
        {"intent": "payment_methods", "count": 87, "example": "Quels modes de paiement acceptez-vous ?"},
        {"intent": "warranty", "count": 76, "example": "Quelle garantie offrez-vous ?"},
        {"intent": "return_policy", "count": 65, "example": "Puis-je retourner le produit ?"},
        {"intent": "technical_support", "count": 54, "example": "Comment obtenir de l'aide technique ?"},
        {"intent": "discount", "count": 43, "example": "Y a-t-il des promotions ?"},
        {"intent": "comparison", "count": 38, "example": "Quelle différence entre X et Y ?"}
    ]
    
    return {"frequent_questions": demo_questions[:limit]}