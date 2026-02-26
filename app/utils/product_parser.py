"""
Parser pour extraire les produits depuis business_data.txt
Format attendu :
    Produit : Nom du produit
    Prix : XXX TND
    Disponibilité : En stock / Rupture de stock
    Livraison : XX jours/heures
"""

import re
from typing import List, Dict, Optional

def parse_business_data(file_path: str = "data/business_data.txt") -> List[Dict]:
    """
    Parse le fichier business_data.txt et extrait les produits
    
    Returns:
        Liste de dictionnaires avec les infos produits
    """
    products = []
    
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # Séparer par blocs (double saut de ligne)
        blocks = content.split('\n\n')
        
        for block in blocks:
            if 'Produit :' not in block:
                continue  # Ignorer les blocs sans produit
            
            product = {}
            lines = block.strip().split('\n')
            
            for line in lines:
                line = line.strip()
                
                if line.startswith('Produit :'):
                    product['name'] = line.replace('Produit :', '').strip()
                
                elif line.startswith('Prix :'):
                    # Extraire le prix (ex: "310 TND" -> 310)
                    price_match = re.search(r'(\d+(?:\.\d+)?)', line)
                    if price_match:
                        product['price'] = float(price_match.group(1))
                
                elif line.startswith('Disponibilité :'):
                    dispo = line.replace('Disponibilité :', '').strip().lower()
                    product['in_stock'] = 'en stock' in dispo
                    product['stock_status'] = line.replace('Disponibilité :', '').strip()
                
                elif line.startswith('Livraison :'):
                    product['delivery_time'] = line.replace('Livraison :', '').strip()
            
            if product.get('name'):  # Ajouter seulement si nom existe
                products.append(product)
        
        print(f"✅ {len(products)} produits chargés depuis {file_path}")
        return products
    
    except FileNotFoundError:
        print(f"❌ Fichier {file_path} introuvable")
        return []
    except Exception as e:
        print(f"❌ Erreur lors du parsing: {e}")
        return []


def get_product_by_name(product_name: str, products: List[Dict]) -> Optional[Dict]:
    """
    Cherche un produit par nom (recherche flexible)
    
    Args:
        product_name: Nom du produit recherché
        products: Liste des produits
    
    Returns:
        Dictionnaire du produit ou None
    """
    product_name_lower = product_name.lower()
    
    # Recherche exacte d'abord
    for product in products:
        if product['name'].lower() == product_name_lower:
            return product
    
    # Recherche partielle
    for product in products:
        if product_name_lower in product['name'].lower():
            return product
    
    return None


def get_available_products(products: List[Dict]) -> List[Dict]:
    """
    Retourne uniquement les produits en stock
    """
    return [p for p in products if p.get('in_stock', False)]


def get_out_of_stock_products(products: List[Dict]) -> List[Dict]:
    """
    Retourne les produits en rupture de stock
    """
    return [p for p in products if not p.get('in_stock', False)]


def format_product_info(product: Dict) -> str:
    """
    Formate les infos d'un produit pour affichage
    
    Returns:
        Texte formaté
    """
    stock_icon = "✅" if product.get('in_stock') else "❌"
    
    return f"""
📦 **{product['name']}**
💰 Prix : {product['price']:.0f} TND
{stock_icon} Disponibilité : {product.get('stock_status', 'Non spécifié')}
🚚 Livraison : {product.get('delivery_time', 'Non spécifié')}
""".strip()


# Test du parser
if __name__ == "__main__":
    products = parse_business_data()
    
    print(f"\n📊 Total produits : {len(products)}")
    print(f"✅ En stock : {len(get_available_products(products))}")
    print(f"❌ Rupture : {len(get_out_of_stock_products(products))}")
    
    print("\n📦 PRODUITS :")
    for p in products:
        print(format_product_info(p))
        print("-" * 50)