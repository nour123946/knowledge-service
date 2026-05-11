"""
Parser pour extraire les produits depuis business_data.txt.

Supporte à la fois:
- le format legacy: Produit / Prix / Disponibilité / Livraison
- le format actuel: Name / Price / Stock / Delivery / Image / Tags
"""

import re
from pathlib import Path
from typing import List, Dict, Optional


DEFAULT_CANDIDATES = [
    Path("data/business_data.txt"),
    Path("uploaded_docs/business_data.txt"),
    Path("data/data_businessv2.txt"),
]


def _resolve_business_data_path(file_path: str | None = None) -> Path:
    if file_path:
        path = Path(file_path)
        if path.exists():
            return path

    for candidate in DEFAULT_CANDIDATES:
        if candidate.exists():
            return candidate

    return Path(file_path or DEFAULT_CANDIDATES[0])


def parse_business_data(file_path: str = "data/business_data.txt") -> List[Dict]:
    """
    Parse le fichier business_data.txt et extrait les produits
    
    Returns:
        Liste de dictionnaires avec les infos produits
    """
    products = []
    
    try:
        path = _resolve_business_data_path(file_path)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        blocks = [block.strip() for block in content.split('PRODUCT:') if block.strip()]

        for block in blocks:
            product: Dict = {}
            lines = [line.strip() for line in block.splitlines() if line.strip()]

            for line in lines:
                if ':' not in line:
                    continue

                key, value = [part.strip() for part in line.split(':', 1)]
                key_lower = key.lower()
                value_lower = value.lower()

                if key_lower in {'id'}:
                    product['id'] = value
                elif key_lower in {'produit', 'name'}:
                    product['name'] = value
                elif key_lower in {'prix', 'price'}:
                    price_match = re.search(r'(\d+(?:\.\d+)?)', value)
                    if price_match:
                        product['price'] = float(price_match.group(1))
                    product['price_text'] = value
                elif key_lower in {'disponibilité', 'stock'}:
                    product['stock_status'] = value
                    product['in_stock'] = ('in stock' in value_lower) or ('en stock' in value_lower)
                elif key_lower in {'livraison', 'delivery'}:
                    product['delivery_time'] = value
                elif key_lower == 'image':
                    product['image'] = value
                elif key_lower == 'brand':
                    product['brand'] = value
                elif key_lower == 'category':
                    product['category'] = value
                elif key_lower == 'sizes':
                    product['sizes'] = value
                elif key_lower == 'color':
                    product['color'] = value
                elif key_lower == 'tags':
                    product['tags'] = value

            if product.get('name'):
                products.append(product)

        print(f"Loaded {len(products)} products from {path}")
        return products
    
    except FileNotFoundError:
        print(f"File not found: {file_path}")
        return []
    except Exception as e:
        print(f"Parsing error: {e}")
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