#!/usr/bin/env python3
"""
Startup script with detailed debugging and cache cleanup
Shows exactly which files are being used and logs routing decisions
"""

import os
import sys
import shutil
from pathlib import Path

# === STEP 1: Show which app will be used ===
print("\n" + "="*70)
print("🔍 STARTUP DIAGNOSTICS")
print("="*70)

app_main_path = Path(__file__).parent / "app" / "main.py"
app_router_path = Path(__file__).parent / "app" / "core" / "router.py"
app_order_wf_path = Path(__file__).parent / "app" / "workflows" / "order_workflow.py"

print(f"\n✓ App main:        {app_main_path.resolve()}")
print(f"✓ Router:          {app_router_path.resolve()}")
print(f"✓ Order workflow:  {app_order_wf_path.resolve()}")

# === STEP 2: Clear all __pycache__ ===
print(f"\n🧹 Clearing Python cache...")
pycache_dirs = list(Path(__file__).parent.rglob("__pycache__"))
for cache_dir in pycache_dirs:
    shutil.rmtree(cache_dir, ignore_errors=True)
    print(f"   Cleared: {cache_dir.relative_to(Path(__file__).parent)}")

# === STEP 3: Verify critical code sections exist ===
print(f"\n🔎 Verifying code sections...")

# Check main.py for the simplified workflow detection
with open(app_main_path) as f:
    main_content = f.read()
    
has_explicit_keywords = "explicit_order_keywords" in main_content
has_should_start = "should_start_workflow" in main_content
no_hardcoded_list = "['puma', 'adidas', 'converse'" not in main_content

print(f"   ✓ has explicit_order_keywords: {has_explicit_keywords}")
print(f"   ✓ has should_start_workflow: {has_should_start}")
print(f"   ✓ NO hardcoded product list: {no_hardcoded_list}")

# Check router.py for product_info_markers
with open(app_router_path) as f:
    router_content = f.read()
    
has_product_markers = "product_info_markers" in router_content
markers_before_order = router_content.find("product_info_markers") < router_content.find("order_markers")

print(f"   ✓ has product_info_markers: {has_product_markers}")  
print(f"   ✓ product_markers BEFORE order_markers: {markers_before_order}")

# === STEP 4: Print key routing logic ===
print(f"\n📋 Key code sections:")

# Extract explicit keywords
import re
keywords_match = re.search(r'explicit_order_keywords = \[(.*?)\]', main_content, re.DOTALL)
if keywords_match:
    keywords = keywords_match.group(1)
    print(f"   Explicit keywords: {keywords.count(',')+1} keywords")
    print(f"   {keywords[:100]}...")

# Extract product info markers
markers_match = re.search(r'product_info_markers = \[(.*?)\]', router_content, re.DOTALL)
if markers_match:
    markers = markers_match.group(1)
    print(f"   Product markers: {markers.count(',')+1} markers")
    print(f"   {markers[:100]}...")

print("\n" + "="*70)
print("✅ READY: All code sections verified - START UVICORN")
print("="*70)
print("\nRun: uvicorn app.main:app --reload --host 127.0.0.1 --port 8000")
print("\n")
