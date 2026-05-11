#!/usr/bin/env python3

from pathlib import Path
from fastapi.testclient import TestClient

import app.main as m
from app.core.memory import get_product_context


def test_customer_upload_image_match_success():
    client = TestClient(m.app)
    image_path = Path("data/images/puma-rsx.jpg")
    assert image_path.exists()

    with image_path.open("rb") as f:
        resp = client.post(
            "/customer/upload-image",
            files={"file": ("puma-rsx.jpg", f, "image/jpeg")},
            data={"session_id": "web_img_test_001", "channel": "web"},
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body.get("matched") is True
    assert body.get("product", {}).get("name") == "Puma RS-X"
    assert body.get("product", {}).get("price")
    assert body.get("product", {}).get("stock")
    assert body.get("product", {}).get("delivery")
    assert body.get("current_product") == "Puma RS-X"

    ctx = get_product_context("web_img_test_001")
    assert ctx.get("current_product") == "Puma RS-X"


def test_customer_upload_image_invalid_format():
    client = TestClient(m.app)
    resp = client.post(
        "/customer/upload-image",
        files={"file": ("test.gif", b"GIF89a", "image/gif")},
        data={"session_id": "web_img_test_002", "channel": "web"},
    )
    assert resp.status_code == 400
