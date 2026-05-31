from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs

import httpx
import pytest

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_USER_ID = "223e4567-e89b-12d3-a456-426614174000"
PRODUCT_ID = "550e8400-e29b-41d4-a716-446655440000"
BLOCKED_PRODUCT_ID = "650e8400-e29b-41d4-a716-446655440000"


def auth_headers(user_id: str = USER_ID) -> dict[str, str]:
    header = _b64({"alg": "none", "typ": "JWT"})
    payload = _b64({"sub": user_id})
    return {"Authorization": f"Bearer {header}.{payload}."}


def product(product_id: str = PRODUCT_ID, title: str = "iPhone 15 Pro Max") -> dict:
    return {
        "id": product_id,
        "slug": "iphone-15-pro-max",
        "title": title,
        "description": "Flagship phone",
        "status": "MODERATED",
        "category": {
            "id": "6ba7b810-9dad-11d1-80b4-00c04fd430c8",
            "name": "Smartphones",
        },
        "images": [{"url": "/s3/iphone15-1.jpg", "ordering": 0}],
        "characteristics": [{"name": "Brand", "value": "Apple"}],
        "skus": [
            {
                "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                "name": "256GB Black",
                "price": 12999000,
                "active_quantity": 5,
                "characteristics": [{"name": "Color", "value": "Black"}],
            }
        ],
    }


async def test_add_to_favorites_returns_201(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/products/{PRODUCT_ID}"
        assert request.headers.get("X-Service-Key") == "test-service-key"
        return httpx.Response(200, json=product())

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())

    assert response.status_code == 201
    body = response.json()
    assert body["product_id"] == PRODUCT_ID
    assert body["user_id"] == USER_ID
    assert body["added_at"].endswith("Z")


async def test_get_favorites_enriched_from_b2b(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/products/{PRODUCT_ID}":
            return httpx.Response(200, json=product())
        assert request.url.path == "/api/v1/products"
        query = parse_qs(request.url.query.decode())
        assert query["ids"] == [PRODUCT_ID]
        return httpx.Response(200, json={"items": [product()], "total_count": 1})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        response = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["product"]["id"] == PRODUCT_ID
    assert body["items"][0]["product"]["skus"][0]["price"] == 12999000


async def test_repeat_add_returns_200_not_duplicate(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/products/{PRODUCT_ID}":
            return httpx.Response(200, json=product())
        return httpx.Response(200, json={"items": [product()], "total_count": 1})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        first = await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        second = await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        listed = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert first.status_code == 201
    assert second.status_code == 200
    body = listed.json()
    assert body["total"] == 1
    assert [item["product"]["id"] for item in body["items"]] == [PRODUCT_ID]


async def test_blocked_product_excluded_from_list(client, b2b_recorder):
    visible = product(PRODUCT_ID, "Visible product")
    blocked = product(BLOCKED_PRODUCT_ID, "Blocked product")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/products/{PRODUCT_ID}":
            return httpx.Response(200, json=visible)
        if request.url.path == f"/api/v1/products/{BLOCKED_PRODUCT_ID}":
            return httpx.Response(200, json=blocked)
        assert request.url.path == "/api/v1/products"
        query = parse_qs(request.url.query.decode())
        assert query["ids"] == [f"{BLOCKED_PRODUCT_ID},{PRODUCT_ID}"]
        return httpx.Response(200, json={"items": [visible], "total_count": 1})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        await ac.post(f"/api/v1/favorites/{BLOCKED_PRODUCT_ID}", headers=auth_headers())
        response = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 1
    assert [item["product"]["id"] for item in body["items"]] == [PRODUCT_ID]


async def test_user_id_from_query_is_ignored(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/products/{PRODUCT_ID}":
            return httpx.Response(200, json=product())
        return httpx.Response(200, json={"items": [product()], "total_count": 1})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        own_response = await ac.get(
            "/api/v1/favorites",
            params={"user_id": OTHER_USER_ID},
            headers=auth_headers(),
        )
        other_response = await ac.get(
            "/api/v1/favorites",
            params={"user_id": USER_ID},
            headers=auth_headers(OTHER_USER_ID),
        )

    assert own_response.status_code == 200
    assert own_response.json()["total"] == 1
    assert other_response.status_code == 200
    assert other_response.json() == {"items": [], "total": 0}


@pytest.mark.parametrize("method", ["post", "get"])
async def test_b2b_unavailable_returns_503_for_favorites(client, b2b_recorder, method):
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unreachable", request=request)

    def available(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=product())

    async with client as ac:
        if method == "post":
            b2b_recorder.set_handler(unavailable)
            response = await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        else:
            b2b_recorder.set_handler(available)
            await ac.post(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
            b2b_recorder.set_handler(unavailable)
            response = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert response.status_code == 503
    assert response.json()["code"] == "B2B_UNAVAILABLE"


def _b64(payload: dict) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
