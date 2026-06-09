from __future__ import annotations

import time
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import jwt
import pytest

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_USER_ID = "223e4567-e89b-12d3-a456-426614174000"
PRODUCT_ID = "550e8400-e29b-41d4-a716-446655440000"
BLOCKED_PRODUCT_ID = "650e8400-e29b-41d4-a716-446655440000"


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


async def test_add_to_favorites_returns_204(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/products/{PRODUCT_ID}"
        assert request.headers.get("X-Service-Key") == "test-service-key"
        return httpx.Response(200, json=product())

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())

    assert response.status_code == 204
    assert response.content == b""


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
        await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        response = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == PRODUCT_ID
    assert body["items"][0]["skus"][0]["price"] == 12999000


async def test_repeat_add_returns_204_not_duplicate(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/products/{PRODUCT_ID}":
            return httpx.Response(200, json=product())
        return httpx.Response(200, json={"items": [product()], "total_count": 1})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        first = await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        second = await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        listed = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert first.status_code == 204
    assert second.status_code == 204
    body = listed.json()
    assert body["total_count"] == 1
    assert [item["id"] for item in body["items"]] == [PRODUCT_ID]


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
        await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        await ac.put(f"/api/v1/favorites/{BLOCKED_PRODUCT_ID}", headers=auth_headers())
        response = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert [item["id"] for item in body["items"]] == [PRODUCT_ID]


async def test_user_id_from_query_is_ignored(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/v1/products/{PRODUCT_ID}":
            return httpx.Response(200, json=product())
        return httpx.Response(200, json={"items": [product()], "total_count": 1})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
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
    assert own_response.json()["total_count"] == 1
    assert other_response.status_code == 200
    assert other_response.json()["total_count"] == 0
    assert other_response.json()["items"] == []


@pytest.mark.parametrize("method", ["put", "get"])
async def test_b2b_unavailable_returns_503_for_favorites(client, b2b_recorder, method):
    def unavailable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unreachable", request=request)

    def available(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=product())

    async with client as ac:
        if method == "put":
            b2b_recorder.set_handler(unavailable)
            response = await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
        else:
            b2b_recorder.set_handler(available)
            await ac.put(f"/api/v1/favorites/{PRODUCT_ID}", headers=auth_headers())
            b2b_recorder.set_handler(unavailable)
            response = await ac.get("/api/v1/favorites", headers=auth_headers())

    assert response.status_code == 503
    assert response.json()["code"] == "B2B_UNAVAILABLE"


async def test_subscribe_returns_204_with_events(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/products/{PRODUCT_ID}"
        return httpx.Response(200, json=product())

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["BACK_IN_STOCK", "PRICE_DROP"]},
            headers=auth_headers(),
        )

    assert response.status_code == 204
    assert response.content == b""


async def test_duplicate_subscription_returns_409(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=product())

    b2b_recorder.set_handler(handler)

    async with client as ac:
        first = await ac.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["BACK_IN_STOCK"]},
            headers=auth_headers(),
        )
        second = await ac.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["PRICE_DROP"]},
            headers=auth_headers(),
        )

    assert first.status_code == 204
    assert second.status_code == 409
    assert second.json()["code"] == "SUBSCRIPTION_ALREADY_EXISTS"


@pytest.mark.parametrize(
    "payload",
    [
        {"events": []},
        {"events": ["WRONG"]},
        {"events": [1]},
        {"events": ["BACK_IN_STOCK", 1]},
        {},
    ],
)
async def test_invalid_notify_on_returns_400(client, b2b_recorder, payload):
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("B2B must not be called when notify_on is invalid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json=payload,
            headers=auth_headers(),
        )

    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_NOTIFY_ON"
    assert b2b_recorder.requests == []


async def test_subscribe_to_unknown_product_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Product not found"})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.post(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            json={"events": ["BACK_IN_STOCK"]},
            headers=auth_headers(),
        )

    assert response.status_code == 404
    assert response.json()["code"] == "PRODUCT_NOT_FOUND"


async def test_unsubscribe_returns_204(client, b2b_recorder):
    async with client as ac:
        response = await ac.delete(
            f"/api/v1/favorites/{PRODUCT_ID}/subscribe",
            headers=auth_headers(),
        )

    assert response.status_code == 204
    assert b2b_recorder.requests == []


async def test_invalid_jwt_returns_401(client, b2b_recorder):
    async with client as ac:
        response = await ac.get(
            "/api/v1/favorites",
            headers={"Authorization": "Bearer invalid.token.value"},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"
