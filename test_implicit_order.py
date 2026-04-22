"""
Test script for implicit order handling (product context memory)
Tests scenarios:
1. Ask about product -> decide to buy (context preserved)
2. Multiple products discussed -> choose one
3. Product mentioned explicitly -> direct buy
4. No context products -> ask which product
"""

from datetime import datetime
from fastapi.testclient import TestClient
from app.core.database import get_database
from app.core.memory import get_product_context, clear_product_context
import app.main as m
import json

c = TestClient(m.app)
db = get_database()

# Clean test sessions
TEST_SESSIONS = ["implicit-test-1", "implicit-test-2", "implicit-test-3", "implicit-test-4"]
for sid in TEST_SESSIONS:
    db["carts"].delete_many({"session_id": sid})
    db["conversations"].delete_many({"session_id": sid})
    db["order_temp_data"].delete_many({"session_id": sid})
    clear_product_context(sid)

print("=" * 80)
print("TEST 1: Ask about product -> decide to buy (context should be preserved)")
print("=" * 80)

sid = "implicit-test-1"
clear_product_context(sid)

# User asks about Adidas
r1 = c.post('/ask', json={
    'query': 'avez vous adidas ?',
    'session_id': sid,
    'channel': 'web'
})
print(f"\nQ1 (Ask about Adidas): {r1.status_code}")
print(f"Response snippet: {r1.json().get('answer', '')[:100]}...")

# Check if product context was tracked
ctx = get_product_context(sid)
print(f"Product context after Q1: candidates={[c['name'] for c in ctx.get('candidates', [])]}")

# User says "je veux commander"
r2 = c.post('/ask', json={
    'query': 'je veux commander',
    'session_id': sid,
    'channel': 'web',
    'conversation_state': 'idle'
})
print(f"\nQ2 (je veux commander): {r2.status_code}")
j2 = r2.json()
print(f"Intent: {j2.get('intent')}")
print(f"Answer: {j2.get('answer', '')[:150]}...")
print(f"Conversation state: {j2.get('conversation_state')}")

# Verify it added product to cart and moved to collecting_name state
expected_state = "collecting_name"
if j2.get('conversation_state') == expected_state and "✅" in (j2.get('answer', '') or ''):
    print(f"✅ TEST 1 PASSED: Product added automatically, moved to {expected_state}")
else:
    print(f"❌ TEST 1 FAILED: Expected state={expected_state}, got {j2.get('conversation_state')}")

print("\n" + "=" * 80)
print("TEST 2: Multiple products in context -> user says 'commander' -> choose menu")
print("=" * 80)

sid = "implicit-test-2"
clear_product_context(sid)

# User asks about Puma
r1 = c.post('/ask', json={
    'query': 'quel est le prix du puma ?',
    'session_id': sid,
    'channel': 'web'
})
print(f"\nQ1 (Ask about Puma): {r1.status_code}")

# User asks about Adidas (different product)
r2 = c.post('/ask', json={
    'query': 'et adidas combien ça coûte ?',
    'session_id': sid,
    'channel': 'web'
})
print(f"Q2 (Ask about Adidas): {r2.status_code}")

ctx = get_product_context(sid)
print(f"Product context after Q2: candidates={[c['name'] for c in ctx.get('candidates', [])]}")

# User says "je veux commander"
r3 = c.post('/ask', json={
    'query': 'je veux commander',
    'session_id': sid,
    'channel': 'web',
    'conversation_state': 'idle'
})
print(f"\nQ3 (je veux commander with multiple products): {r3.status_code}")
j3 = r3.json()
print(f"Intent: {j3.get('intent')}")
print(f"Answer: {j3.get('answer', '')[:200]}...")
print(f"Conversation state: {j3.get('conversation_state')}")

# Verify it's now in choosing_product state
if "choosing_product" in (j3.get('conversation_state') or '') and "1" in (j3.get('answer', '') or ''):
    print(f"✅ TEST 2 PASSED: Multiple products → choice menu presented")
else:
    print(f"❌ TEST 2 FAILED: Expected choosing_product state with menu, got: {j3.get('conversation_state')}")

print("\n" + "=" * 80)
print("TEST 3: Explicit product mention + commander -> direct add to cart")
print("=" * 80)

sid = "implicit-test-3"
clear_product_context(sid)

# User says "je veux commander converse"
r1 = c.post('/ask', json={
    'query': 'je veux commander converse',
    'session_id': sid,
    'channel': 'web',
    'conversation_state': 'idle'
})
print(f"\nQ1 (je veux commander converse): {r1.status_code}")
j1 = r1.json()
print(f"Intent: {j1.get('intent')}")
print(f"Answer: {j1.get('answer', '')[:150]}...")
print(f"Conversation state: {j1.get('conversation_state')}")

# Verify direct add + move to collecting_name
if j1.get('conversation_state') == "collecting_name" and "Converse" in (j1.get('answer', '') or ''):
    print(f"✅ TEST 3 PASSED: Explicit product → direct add, moved to collecting_name")
else:
    print(f"❌ TEST 3 FAILED: Expected collecting_name, got {j1.get('conversation_state')}")

print("\n" + "=" * 80)
print("TEST 4: No context + commander -> ask which product")
print("=" * 80)

sid = "implicit-test-4"
clear_product_context(sid)

# User says "je veux commander" with no prior context
r1 = c.post('/ask', json={
    'query': 'je veux commander',
    'session_id': sid,
    'channel': 'web',
    'conversation_state': 'idle'
})
print(f"\nQ1 (je veux commander with no context): {r1.status_code}")
j1 = r1.json()
print(f"Intent: {j1.get('intent')}")
print(f"Answer: {j1.get('answer', '')[:150]}...")
print(f"Conversation state: {j1.get('conversation_state')}")

# Verify it asks which product
if "asking_product" in (j1.get('conversation_state') or '') and ("Quel produit" in (j1.get('answer', '') or '') or "product" in (j1.get('answer', '') or '').lower()):
    print(f"✅ TEST 4 PASSED: No context → asking_product state")
else:
    print(f"❌ TEST 4 FAILED: Expected asking_product state, got {j1.get('conversation_state')}")
    
print("\n" + "=" * 80)
print("SUMMARY")
print("=" * 80)
print("✅ All implicit order tests completed. Check results above.")
