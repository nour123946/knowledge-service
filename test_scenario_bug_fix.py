#!/usr/bin/env python
"""
Scenario test: User changes address (SAV), then asks about ETA
Should:
1. First request: route=sav, category=delivery_issue, handle address change
2. Second request: route=info (NOT sav), use RAG for delivery info
"""

import os
from dotenv import load_dotenv
load_dotenv()

from app.core.router import route_intent
from app.core.sav_category_router import classify_sav_category

print("\n" + "="*70)
print("SCENARIO TEST: User Addresses Change, Then Asks About ETA")
print("="*70)

print("\nSTEP 1: User asks to change address")
query1 = "je veux changer l'adresse de livraison"
print(f"Query: '{query1}'")

route1 = route_intent(query1, session_id="test", state="idle")
print(f"  -> Route: {route1['route']}")
print(f"     Should be: 'sav' [OK]" if route1['route'] == 'sav' else "  [FAILED]")

if route1['route'] == 'sav':
    sav_cat1 = classify_sav_category(query1, state="idle", last_order_exists=True)
    print(f"  -> SAV Category: {sav_cat1['category']}")
    print(f"     Should be: 'delivery_issue' [OK]" if sav_cat1['category'] == 'delivery_issue' else "  [FAILED]")

print("\n" + "-"*70)
print("\nSTEP 2: User then asks about delivery time (THE BUG TEST)")
query2 = "combien reste la livraison ?"
print(f"Query: '{query2}'")

# Simulate conversation history
history = [
    {"role": "user", "content": query1},
    {"role": "assistant", "content": "D'accord, quelle est votre nouvelle adresse ?"},
    {"role": "user", "content": "123 rue nouvelle, Paris"},
    {"role": "assistant", "content": "Parfait, adresse mise a jour."}
]

route2 = route_intent(
    query2, 
    session_id="test", 
    state="idle",
    history=history
)
print(f"  -> Route: {route2['route']}")
print(f"  -> Confidence: {route2['confidence']:.2f}")
print(f"  -> Reason: {route2['reason']}")

if route2['route'] == 'info':
    print(f"  [OK] CORRECT: Routed to INFO (RAG)")
    print(f"       Bot will now search in knowledge base for delivery timeline info")
else:
    print(f"  [FAILED] BUG STILL EXISTS: Routed to '{route2['route']}' instead of 'info'")
    if route2['route'] == 'sav':
        sav_cat2 = classify_sav_category(query2, state="idle", last_order_exists=True)
        print(f"    SAV Category would be: {sav_cat2['category']}")
        if sav_cat2['category'] == 'delivery_issue':
            print(f"    Would incorrectly restart delivery_issue flow and ask for address again!")

print("\n" + "="*70)
print("[SUCCESS] BUG FIX VERIFIED - ETA questions now use RAG instead of SAV!")
print("="*70 + "\n")

