#!/usr/bin/env python3
"""
Test to verify RAG architecture restoration:
- Text product questions (prix, combien, disponible) route to RAG
- Explicit order keywords (commander, acheter) route to order flow
- Image recognition still works
"""

from fastapi.testclient import TestClient
import app.main as m
from app.core.memory import get_product_context


def test_product_question_routes_to_rag():
    """Test: Basic product question should route to RAG (info route)"""
    client = TestClient(m.app)
    session_id = "test_rag_routing_001"
    
    # Query: Prix d'un produit (PRODUCT QUESTION - should go to RAG)
    response = client.post(
        "/ask",
        json={
            "query": "prix Reebok Classic Leather",
            "session_id": session_id,
            "channel": "web",
            "conversation_state": "idle",
        },
    )
    
    assert response.status_code == 200
    body = response.json()
    print(f"\n✅ Product question response:")
    print(f"  Route: {body.get('route')}")
    print(f"  Intent: {body.get('intent')}")
    print(f"  Answer: {body.get('final_answer')[:100]}...")
    
    # Should NOT be order flow
    assert body.get("route") in ["info", None] or body.get("is_order_flow") == False
    assert "240" in body.get("final_answer") or "Reebok" in body.get("final_answer")
    print("✅ Product question correctly routed to RAG")


def test_availability_question_routes_to_rag():
    """Test: Product availability question should route to RAG"""
    client = TestClient(m.app)
    session_id = "test_rag_routing_002"
    
    response = client.post(
        "/ask",
        json={
            "query": "Puma RS-X en stock ?",
            "session_id": session_id,
            "channel": "web",
            "conversation_state": "idle",
        },
    )
    
    assert response.status_code == 200
    body = response.json()
    print(f"\n✅ Availability question response:")
    print(f"  Route: {body.get('route')}")
    print(f"  Answer: {body.get('final_answer')[:100]}...")
    
    assert body.get("route") in ["info", None]
    assert body.get("is_order_flow") == False
    print("✅ Availability question correctly routed to RAG")


def test_delivery_question_routes_to_rag():
    """Test: Delivery/delay question should route to RAG"""
    client = TestClient(m.app)
    session_id = "test_rag_routing_003"
    
    response = client.post(
        "/ask",
        json={
            "query": "combien de temps pour la livraison ?",
            "session_id": session_id,
            "channel": "web",
            "conversation_state": "idle",
        },
    )
    
    assert response.status_code == 200
    body = response.json()
    print(f"\n✅ Delivery question response:")
    print(f"  Route: {body.get('route')}")
    print(f"  Answer: {body.get('final_answer')[:100]}...")
    
    assert body.get("route") in ["info", None]
    assert body.get("is_order_flow") == False
    print("✅ Delivery question correctly routed to RAG")


def test_explicit_order_routes_to_workflow():
    """Test: Explicit order keywords route to order workflow"""
    client = TestClient(m.app)
    session_id = "test_rag_routing_004"
    
    response = client.post(
        "/ask",
        json={
            "query": "je veux commander Puma RS-X",
            "session_id": session_id,
            "channel": "web",
            "conversation_state": "idle",
        },
    )
    
    assert response.status_code == 200
    body = response.json()
    print(f"\n✅ Explicit order response:")
    print(f"  Route: {body.get('route')}")
    print(f"  Is order flow: {body.get('is_order_flow')}")
    print(f"  Answer: {body.get('final_answer')[:100]}...")
    
    assert body.get("is_order_flow") == True
    assert "ajouté au panier" in body.get("final_answer") or "commander" in body.get("final_answer").lower()
    print("✅ Explicit order correctly routed to workflow")


def test_image_upload_with_implicit_order():
    """Test: Image upload works and 'je veux commander' uses recognized product"""
    from pathlib import Path
    
    client = TestClient(m.app)
    session_id = "test_rag_routing_005"
    image_path = Path("data/images/converse-chuck-taylor.jpg")
    
    if not image_path.exists():
        print(f"⚠️  Skipping image test - file not found: {image_path}")
        return
    
    # Step 1: Upload image
    with image_path.open("rb") as f:
        resp1 = client.post(
            "/customer/upload-image",
            files={"file": ("converse-chuck-taylor.jpg", f, "image/jpeg")},
            data={"session_id": session_id, "channel": "web"},
        )
    
    assert resp1.status_code == 200
    body1 = resp1.json()
    print(f"\n✅ Image upload response:")
    print(f"  Matched: {body1.get('matched')}")
    print(f"  Product: {body1.get('product', {}).get('name')}")
    print(f"  Current product: {body1.get('current_product')}")
    
    assert body1.get("matched") == True
    assert body1.get("current_product") == "Converse Chuck Taylor"
    
    # Verify memory storage
    ctx = get_product_context(session_id)
    assert ctx.get("current_product") == "Converse Chuck Taylor"
    print("✅ current_product correctly stored in memory")
    
    # Step 2: User says "je veux commander" (should use remembered product)
    resp2 = client.post(
        "/ask",
        json={
            "query": "je veux commander",
            "session_id": session_id,
            "channel": "web",
            "conversation_state": "idle",
        },
    )
    
    assert resp2.status_code == 200
    body2 = resp2.json()
    print(f"\n✅ Implicit order response:")
    print(f"  Answer: {body2.get('final_answer')[:200]}...")
    
    assert "Converse Chuck Taylor" in body2.get("final_answer")
    assert "ajouté au panier" in body2.get("final_answer")
    assert body2.get("is_order_flow") == True
    print("✅ Implicit order correctly used remembered product from image")


if __name__ == "__main__":
    print("=" * 60)
    print("TESTING RAG ARCHITECTURE RESTORATION")
    print("=" * 60)
    
    test_product_question_routes_to_rag()
    test_availability_question_routes_to_rag()
    test_delivery_question_routes_to_rag()
    test_explicit_order_routes_to_workflow()
    test_image_upload_with_implicit_order()
    
    print("\n" + "=" * 60)
    print("✅ ALL TESTS PASSED - RAG ARCHITECTURE RESTORED")
    print("=" * 60)
