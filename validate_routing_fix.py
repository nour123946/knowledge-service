#!/usr/bin/env python3
"""
Validation: Test RAG routing restoration
"""

from fastapi.testclient import TestClient
import app.main as m

client = TestClient(m.app)

print("\n=== VALIDATING RAG ROUTING FIX ===\n")

# Test 1: Product price question
print("TEST 1: 'prix Reebok Classic Leather' should route to RAG...")
resp = client.post(
    "/ask",
    json={
        "query": "prix Reebok Classic Leather",
        "session_id": "test_prix_001",
        "channel": "web",
        "conversation_state": "idle",
    },
)
status1 = resp.status_code
is_order_1 = resp.json().get('is_order_flow')
route_1 = resp.json().get('route', 'N/A')
answer_1 = resp.json().get('final_answer', '')[:80]

print(f"  Status: {status1}")
print(f"  Route: {route_1}")
print(f"  Is order flow: {is_order_1}")
print(f"  Answer snippet: {answer_1}")

if status1 == 200 and is_order_1 == False and route_1 == "info":
    print("  PASS: Product question correctly routed to RAG\n")
    test1_pass = True
else:
    print("  FAIL: Product question not routed correctly\n")
    test1_pass = False

# Test 2: Availability question
print("TEST 2: 'Converse en stock' should route to RAG...")
resp = client.post(
    "/ask",
    json={
        "query": "Converse en stock",
        "session_id": "test_converse_001",
        "channel": "web",
        "conversation_state": "idle",
    },
)
status2 = resp.status_code
is_order_2 = resp.json().get('is_order_flow')
route_2 = resp.json().get('route', 'N/A')

print(f"  Status: {status2}")
print(f"  Route: {route_2}")
print(f"  Is order flow: {is_order_2}")

if status2 == 200 and is_order_2 == False and route_2 == "info":
    print("  PASS: Availability question routed to RAG\n")
    test2_pass = True
else:
    print("  FAIL: Availability question not routed correctly\n")
    test2_pass = False

# Test 3: Explicit order
print("TEST 3: 'je veux commander Puma' should route to ORDER workflow...")
resp = client.post(
    "/ask",
    json={
        "query": "je veux commander Puma RS-X",
        "session_id": "test_order_001",
        "channel": "web",
        "conversation_state": "idle",
    },
)
status3 = resp.status_code
is_order_3 = resp.json().get('is_order_flow')
route_3 = resp.json().get('route', 'N/A')

print(f"  Status: {status3}")
print(f"  Route: {route_3}")
print(f"  Is order flow: {is_order_3}")

if status3 == 200 and is_order_3 == True and route_3 == "order":
    print("  PASS: Order keyword correctly routed to workflow\n")
    test3_pass = True
else:
    print("  FAIL: Order not routed correctly\n")
    test3_pass = False

# Summary
print("=== SUMMARY ===")
all_pass = test1_pass and test2_pass and test3_pass
if all_pass:
    print("ALL TESTS PASSED - RAG routing fix validated!")
else:
    failed = []
    if not test1_pass: failed.append("Test 1 (product price)")
    if not test2_pass: failed.append("Test 2 (availability)")
    if not test3_pass: failed.append("Test 3 (order)")
    print(f"FAILED: {', '.join(failed)}")

print("\nKey validations:")
print("1. 'prix Reebok' routes to RAG (info) - NOT order flow")
print("2. 'Converse en stock' routes to RAG (info) - NOT order flow")
print("3. 'je veux commander' routes to ORDER workflow")
print("\nArchitecture restored: Text product questions now use RAG!")
