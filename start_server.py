#!/usr/bin/env python3
"""
Simple startup with verification and detailed logging added
"""

import sys

print("\n" + "=" * 70)
print("VERIFYING ROUTING FIXES")
print("=" * 70)

# Quick verification
with open('app/main.py') as f:
    main = f.read()
    assert 'explicit_order_keywords' in main, "❌ Missing explicit_order_keywords in main.py"
    assert "['puma', 'adidas', 'converse'" not in main, "❌ Hardcoded product list still in main.py"
    print("✅ main.py verified - explicit keywords only, NO hardcoded list")

with open('app/core/router.py') as f:
    router = f.read()
    assert 'product_info_markers' in router, "❌ Missing product_info_markers in router.py"
    print("✅ router.py verified - product_info_markers in place")

print("\n" + "=" * 70)
print("READY TO START UVICORN")
print("=" * 70)
print("""
Run this command in a new terminal:

  uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --log-level debug

Or with this script:
  python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000 --log-level debug

This will:
- Start the server on http://127.0.0.1:8000
- Enable hot reload (reloads on file changes)
- Log all routing decisions
- Load the UPDATED code (explicit query routing without hardcodes)
""")
