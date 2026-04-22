#!/usr/bin/env python
"""
Test script to verify ETA/delay questions are correctly routed to INFO
and not incorrectly routed to SAV delivery_issue.
"""

import sys
import os
import logging

# Load environment variables BEFORE importing app modules
from dotenv import load_dotenv
load_dotenv()

from app.core.router import route_intent, _fallback_route
from app.core.sav_category_router import classify_sav_category

logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger("test_eta_routing")

# Color codes for terminal output
GREEN = '\033[92m'
RED = '\033[91m'
YELLOW = '\033[93m'
BLUE = '\033[94m'
RESET = '\033[0m'

def test_router_eta_questions():
    """Test that ETA/delay questions are routed to INFO"""
    print(f"\n{BLUE}=== Test 1: Router - ETA/Delay Questions ==={RESET}")
    
    eta_queries = [
        "combien reste la livraison ?",
        "c'est pour quand la livraison ?",
        "délai de livraison ?",
        "quand va arriver ma commande ?",
        "pour quand l'arrivée ?",
        "dans combien de temps ça arrive ?",
        "combien de temps avant la livraison ?",
        "ETA livraison ?",
        "quand est-ce que je vais recevoir ?",
        "reste combien de jours avant livraison ?"
    ]
    
    results = []
    for query in eta_queries:
        route_result = route_intent(query, session_id="test", state="idle")
        route = route_result.get("route")
        confidence = route_result.get("confidence")
        reason = route_result.get("reason")
        
        is_correct = route == "info"
        mark = f"{GREEN}✓{RESET}" if is_correct else f"{RED}✗{RESET}"
        
        results.append((query, route, confidence, is_correct))
        print(f"{mark} '{query}'")
        print(f"   → route={route}, confidence={confidence:.2f}, reason={reason}")
    
    correct_count = sum(1 for _, _, _, is_correct in results if is_correct)
    total = len(results)
    print(f"\n{GREEN if correct_count == total else YELLOW}Passed {correct_count}/{total}{RESET}")
    return correct_count == total

def test_fallback_eta_detection():
    """Test that fallback route detects ETA markers"""
    print(f"\n{BLUE}=== Test 2: Fallback - ETA Marker Detection ==={RESET}")
    
    eta_queries = [
        "combien reste la livraison ?",
        "délai de livraison",
        "c'est pour quand ?",
        "quand arrive ma commande ?"
    ]
    
    results = []
    for query in eta_queries:
        fallback_result = _fallback_route(query, state="idle")
        route = fallback_result.get("route")
        confidence = fallback_result.get("confidence")
        
        is_correct = route == "info" and confidence >= 0.90
        mark = f"{GREEN}✓{RESET}" if is_correct else f"{RED}✗{RESET}"
        
        results.append((query, route, confidence, is_correct))
        print(f"{mark} '{query}'")
        print(f"   → route={route}, confidence={confidence:.2f}")
    
    correct_count = sum(1 for _, _, _, is_correct in results if is_correct)
    total = len(results)
    print(f"\n{GREEN if correct_count == total else YELLOW}Passed {correct_count}/{total}{RESET}")
    return correct_count == total

def test_sav_delivery_vs_eta():
    """Test that SAV questions go to delivery_issue, but ETA questions don't"""
    print(f"\n{BLUE}=== Test 3: SAV Category - delivery_issue vs ETA ==={RESET}")
    
    test_cases = [
        # Real SAV delivery_issue queries
        ("où est mon colis ?", "delivery_issue", True),
        ("suivi de ma commande", "delivery_issue", True),
        ("pas reçu ma commande", "delivery_issue", True),
        ("retard livraison", "delivery_issue", True),
        ("je veux changer l'adresse de livraison", "delivery_issue", True),
        
        # ETA/delay queries that should NOT be delivery_issue
        ("quand arrive ma livraison ?", "unknown", True),
        ("combien de temps avant livraison ?", "unknown", True),
        ("délai de livraison ?", "unknown", True),
        ("c'est pour quand ?", "unknown", True),
    ]
    
    results = []
    for query, expected_category, should_avoid_delivery_issue in test_cases:
        sav_result = classify_sav_category(query, state="idle", last_order_exists=True)
        category = sav_result.get("category")
        confidence = sav_result.get("confidence")
        
        # For ETA queries, we expect "unknown" or at minimum NOT "delivery_issue"
        if should_avoid_delivery_issue:
            is_correct = category != "delivery_issue"
        else:
            is_correct = category == expected_category
        
        mark = f"{GREEN}✓{RESET}" if is_correct else f"{RED}✗{RESET}"
        
        results.append((query, category, expected_category, is_correct))
        print(f"{mark} '{query}'")
        print(f"   → category={category}, confidence={confidence:.2f}")
        print(f"      Expected NOT: delivery_issue, Got: {category}")
    
    correct_count = sum(1 for _, _, _, is_correct in results if is_correct)
    total = len(results)
    print(f"\n{GREEN if correct_count == total else YELLOW}Passed {correct_count}/{total}{RESET}")
    return correct_count == total

def test_router_sav_queries():
    """Test that real SAV queries still route to SAV"""
    print(f"\n{BLUE}=== Test 4: Router - Real SAV Queries ==={RESET}")
    
    sav_queries = [
        "où est mon colis ?",
        "suivi de mon colis",
        "pas reçu ma commande",
        "retard de livraison",
        "je veux changer l'adresse",
        "comment faire un retour ?",
        "je veux faire un remboursement"
    ]
    
    results = []
    for query in sav_queries:
        route_result = route_intent(query, session_id="test", state="idle")
        route = route_result.get("route")
        confidence = route_result.get("confidence")
        
        # Note: Some queries might route to "info" if they're too FAQ-like
        # but genuine SAV actions should route to "sav"
        is_sav = route == "sav"
        mark = f"{GREEN}✓{RESET}" if is_sav else f"{YELLOW}⚠{RESET}"
        
        results.append((query, route, confidence, is_sav))
        print(f"{mark} '{query}'")
        print(f"   → route={route}, confidence={confidence:.2f}")
    
    sav_count = sum(1 for _, _, _, is_sav in results if is_sav)
    total = len(results)
    print(f"\nSAV routes: {sav_count}/{total}")
    return sav_count >= (total - 1)  # Allow 1-2 edge cases

def test_edge_case_after_sav_flow():
    """
    Test the real scenario: 
    1. User asks about delivery address (delivery_issue)
    2. Bot handles it
    3. User then asks "combien reste la livraison ?" 
    => Should route to INFO/RAG, NOT back to SAV
    """
    print(f"\n{BLUE}=== Test 5: Edge Case - After SAV, Then ETA Question ==={RESET}")
    
    # Simulate conversation history
    history = [
        {"role": "user", "content": "je veux changer l'adresse de livraison"},
        {"role": "assistant", "content": "D'accord, j'ai noté. Votre nouvelle adresse ?"},
        {"role": "user", "content": "123 rue nouvelle, Paris"},
        {"role": "assistant", "content": "Parfait, adresse mise à jour."},
        {"role": "user", "content": "combien reste la livraison ?"}
    ]
    
    # Get just the user query
    query = history[-1]["content"]
    router_history = history[-4:-1]  # Only assistant context for router
    
    route_result = route_intent(query, session_id="test", state="idle", history=router_history)
    route = route_result.get("route")
    confidence = route_result.get("confidence")
    reason = route_result.get("reason")
    
    is_correct = route == "info"
    mark = f"{GREEN}✓{RESET}" if is_correct else f"{RED}✗{RESET}"
    
    print(f"{mark} Query after SAV flow: '{query}'")
    print(f"   → route={route}, confidence={confidence:.2f}, reason={reason}")
    print(f"   Expected: route=info (to use RAG for delivery timeline)")
    
    return is_correct

if __name__ == "__main__":
    print(f"\n{YELLOW}{'='*60}")
    print("ETA/Delay Routing Fix - Test Suite")
    print(f"{'='*60}{RESET}")
    
    all_pass = True
    
    try:
        all_pass &= test_router_eta_questions()
    except Exception as e:
        print(f"{RED}❌ Test 1 failed: {e}{RESET}")
        all_pass = False
    
    try:
        all_pass &= test_fallback_eta_detection()
    except Exception as e:
        print(f"{RED}❌ Test 2 failed: {e}{RESET}")
        all_pass = False
    
    try:
        all_pass &= test_sav_delivery_vs_eta()
    except Exception as e:
        print(f"{RED}❌ Test 3 failed: {e}{RESET}")
        all_pass = False
    
    try:
        all_pass &= test_router_sav_queries()
    except Exception as e:
        print(f"{RED}❌ Test 4 failed: {e}{RESET}")
        all_pass = False
    
    try:
        all_pass &= test_edge_case_after_sav_flow()
    except Exception as e:
        print(f"{RED}❌ Test 5 failed: {e}{RESET}")
        all_pass = False
    
    print(f"\n{YELLOW}{'='*60}")
    if all_pass:
        print(f"{GREEN}✓ All tests passed!{RESET}")
    else:
        print(f"{RED}❌ Some tests failed - Review implementation{RESET}")
    print(f"{'='*60}{RESET}\n")
    
    sys.exit(0 if all_pass else 1)
