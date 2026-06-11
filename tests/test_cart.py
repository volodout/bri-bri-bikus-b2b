from __future__ import annotations

import time
from uuid import uuid4

import httpx
import jwt

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
SESSION_ID = "guest-session-1"
PRODUCT_ID = "550e8400-e29b-41d4-a716-446655440000"
SKU_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def auth_headers(user_id: str = USER_ID) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {
            "sub": user_id,
            "role": "buyer",
            "iat": now,
            "exp": now + 3600,
            "jti": str(uuid4()),
        },
        "dev-jwt-secret-for-tests-32-bytes",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def session_headers(session_id: str = SESSION_ID) -> dict[str, str]:
    return {"X-Session-Id": session_id}


def product_payload(*, active_quantity: int = 5, status: str = "MODERATED", price: int = 12999000) -> dict:
    return {
        "id": PRODUCT_ID,
        "title": "iPhone 15 Pro Max",
        "description": "Flagship phone",
        "status": status,
        "images": [{"url": "/s3/iphone15-1.jpg", "ordering": 0}],
        "characteristics": [],
        "skus": [
            {
                "id": SKU_ID,
                "name": "256GB Black",
                "price": price,
                "active_quantity": active_quantity,
                "image": "/s3/iphone15-black.jpg",
                "characteristics": [],
            }
        ],
    }


def sku_payload(*, active_quantity: int = 5) -> dict:
    product = product_payload(active_quantity=active_quantity)
    return {"product": product, "sku": product["skus"][0]}


async def test_add_sku_increments_quantity_if_already_in_cart(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/skus/{SKU_ID}":
            return httpx.Response(200, json=sku_payload(active_quantity=10))
        if request.url.path == "/api/v1/products":
            return httpx.Response(200, json={"items": [product_payload(active_quantity=10)]})
        raise AssertionError(f"unexpected upstream path {request.url.path}")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        first = await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 1},
            headers=auth_headers(),
        )
        second = await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 2},
            headers=auth_headers(),
        )
        cart = await ac.get("/api/v1/cart", headers=auth_headers())

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["items"][0]["quantity"] == 3
    assert cart.json()["items"][0]["quantity"] == 3


async def test_get_cart_enriched_with_b2b_data(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/skus/{SKU_ID}":
            return httpx.Response(200, json=sku_payload(active_quantity=5))
        if request.url.path == "/api/v1/products":
            return httpx.Response(200, json={"items": [product_payload(active_quantity=5, price=1000)]})
        raise AssertionError(f"unexpected upstream path {request.url.path}")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 2},
            headers=session_headers(),
        )
        response = await ac.get("/api/v1/cart", headers=session_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["product_id"] == PRODUCT_ID
    assert body["items"][0]["sku_name"] == "256GB Black"
    assert body["items"][0]["unit_price"] == 1000
    assert body["items"][0]["line_total"] == 2000
    assert body["summary"]["total_amount"] == 2000
    assert body["checkout_payload"]["items"][0]["sku_id"] == SKU_ID


async def test_get_cart_item_enriched_with_b2b_data(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/skus/{SKU_ID}":
            return httpx.Response(200, json=sku_payload(active_quantity=5))
        if request.url.path == "/api/v1/products":
            return httpx.Response(200, json={"items": [product_payload(active_quantity=5, price=1000)]})
        raise AssertionError(f"unexpected upstream path {request.url.path}")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        added = await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 2},
            headers=session_headers(),
        )
        item_id = added.json()["items"][0]["item_id"]
        response = await ac.get(f"/api/v1/cart/items/{item_id}", headers=session_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["item_id"] == item_id
    assert body["sku_id"] == SKU_ID
    assert body["unit_price"] == 1000
    assert body["line_total"] == 2000


async def test_unavailable_sku_shown_with_reason(client, b2b_recorder):
    state = {"stock": 5}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/skus/{SKU_ID}":
            return httpx.Response(200, json=sku_payload(active_quantity=5))
        if request.url.path == "/api/v1/products":
            return httpx.Response(200, json={"items": [product_payload(active_quantity=state["stock"])]})
        raise AssertionError(f"unexpected upstream path {request.url.path}")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 1},
            headers=auth_headers(),
        )
        state["stock"] = 0
        response = await ac.get("/api/v1/cart", headers=auth_headers())

    item = response.json()["items"][0]
    assert item["is_available"] is False
    assert item["unavailable_reason"] == "OUT_OF_STOCK"
    assert item["line_total"] == 0
    assert response.json()["summary"]["total_amount"] == 0


async def test_guest_cart_merged_on_login(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/skus/{SKU_ID}":
            return httpx.Response(200, json=sku_payload(active_quantity=10))
        if request.url.path == "/api/v1/products":
            return httpx.Response(200, json={"items": [product_payload(active_quantity=10)]})
        raise AssertionError(f"unexpected upstream path {request.url.path}")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 2},
            headers=session_headers(),
        )
        await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 5},
            headers=auth_headers(),
        )
        headers = auth_headers() | session_headers()
        merged = await ac.get("/api/v1/cart", headers=headers)
        guest_after = await ac.get("/api/v1/cart", headers=session_headers())

    body = merged.json()
    assert merged.status_code == 200
    assert len(body["items"]) == 1
    assert body["items"][0]["quantity"] == 5
    assert guest_after.json()["items"] == []


async def test_patch_item_updates_quantity_by_sku_id(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/skus/{SKU_ID}":
            return httpx.Response(200, json=sku_payload(active_quantity=10))
        if request.url.path == "/api/v1/products":
            return httpx.Response(200, json={"items": [product_payload(active_quantity=10)]})
        raise AssertionError(f"unexpected upstream path {request.url.path}")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.post(
            "/api/v1/cart/items",
            json={"sku_id": SKU_ID, "quantity": 1},
            headers=auth_headers(),
        )
        response = await ac.patch(
            f"/api/v1/cart/items/{SKU_ID}",
            json={"quantity": 5},
            headers=auth_headers(),
        )

    assert response.status_code == 200
    body = response.json()
    assert body["item"]["sku_id"] == SKU_ID
    assert body["item"]["quantity"] == 5
    assert "summary" in body
