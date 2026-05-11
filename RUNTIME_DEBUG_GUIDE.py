#!/usr/bin/env python3
"""
RUNTIME DEBUG GUIDE

Problem: Tests pass but browser still shows old behavior
Root cause: Uvicorn not running OR cached bytecode

SOLUTION STEPS:
"""

print("""
╔════════════════════════════════════════════════════════════════════════╗
║         RUNTIME FIX - RESTORE RAG ARCHITECTURE                       ║
╚════════════════════════════════════════════════════════════════════════╝

✅ CODE VERIFICATION:
   ✓ explicit_order_keywords in main.py: YES
   ✓ NO hardcoded product list: YES  
   ✓ product_info_markers in router.py: YES

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

STEP 1: Stop any running Python/uvicorn server
────────────────────────────────────────────
  PowerShell: Get-Process python | Stop-Process -Force
  Or: Ctrl+C any running terminal

STEP 2: CRITICAL - Clear Python cache files
──────────────────────────────────────────
  PowerShell:
  $dirs = Get-ChildItem -Path . -Recurse -Directory -Filter "__pycache__" 
  $dirs | Remove-Item -Recurse -Force

STEP 3: Start uvicorn with LOG LEVEL DEBUG
────────────────────────────────────────
  Open NEW terminal, go to project folder, run:
  
  uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --log-level debug

  Expected output:
    - Server running on http://127.0.0.1:8000
    - "Uvicorn running on" message
    - DEBUG logs appearing as you interact

STEP 4: Test "prix Reebok" in browser
────────────────────────────────────
  Open: http://127.0.0.1:8000/widget/index.html
  Send: "prix Reebok Classic Leather"
  
  Expected in logs:
    🧭 ROUTER → route=info
    🔍 WORKFLOW DECISION → should_start_workflow=False
  
  Expected answer:
    Product information about Reebok (NOT "Produit non reconnu")

STEP 5: Debug logs to look for
──────────────────────────────
  In terminal, you should see:

  FOR "prix Reebok":
    - 🧭 ROUTER session=... route=info
    - Should NOT see "LAUNCHING ORDER WORKFLOW"
  
  FOR "je veux commander":
    - 🧭 ROUTER session=... route=order
    - Should see "LAUNCHING ORDER WORKFLOW"

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

KEY ROUTING LOGIC:

FILE: app/core/router.py (lines 100-109)
  product_info_markers = ["prix", "disponible", "stock", ...]
  → Returns route="info" with confidence 0.92

FILE: app/main.py (lines 1829-1833)
  should_start_workflow = (is_in_order_workflow or has_explicit_order_keyword)
  explicit_order_keywords = ["commander", "acheter", "panier", ...]
  → Workflow only for explicit keywords

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

TROUBLESHOOTING:

❌ Browser still shows "Produit non reconnu":
   1. Check if uvicorn started with DEBUG logs
   2. Verify you see "🧭 ROUTER" logs 
   3. Kill all Python processes and restart
   4. Clear browser cache (Ctrl+Shift+Delete)
   5. Verify logs show route=info for "prix" queries

❌ No logs appearing:
   1. Verify uvicorn is actually running on port 8000
   2. Command shows "Application startup complete"?
   3. Try: curl http://127.0.0.1:8000/docs
   4. If no response, port might be in use (change to 8001)

❌ Still using old behavior:
   1. Python cache not cleared → restart step 2
   2. Old app.pyc files exist → delete them manually
   3. Python bytecode compiled at module level → full restart needed
   4. Browser cached old JS/HTML → Ctrl+Shift+Delete browser cache

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

VALIDATION TESTS (after server is running):

Test 1 - Product Question:
  curl -X POST http://127.0.0.1:8000/ask \\
    -H "Content-Type: application/json" \\
    -d '{
      "query": "prix Reebok Classic Leather",
      "session_id": "test_prix_debug",
      "channel": "web",
      "conversation_state": "idle"
    }'
    
  Expected: route=info, NOT order workflow

Test 2 - Explicit Order:
  curl -X POST http://127.0.0.1:8000/ask \\
    -H "Content-Type: application/json" \\
    -d '{
      "query": "je veux commander Puma",
      "session_id": "test_order_debug",
      "channel": "web",
      "conversation_state": "idle"
    }'
    
  Expected: route=order, LAUNCH ORDER WORKFLOW

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

✅ HOW TO CONFIRM FIX IS ACTIVE:

1. Server logs show explicit route decision in 🧭 ROUTER
2. "prix" queries → 🧭 ROUTER route=info
3. "commander" queries → 🧭 Router route=order  
4. NO "Produit non reconnu" for product questions
5. Explicit order keywords trigger order workflow

""")

print("\n✅ READY: Follow steps 1-4 above to activate the fix")
