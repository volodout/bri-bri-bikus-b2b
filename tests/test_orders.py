from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Callable
from uuid import uuid4

import httpx
import jwt

from app.addresses import Address

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
SKU_1 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SKU_2 = "8a4e3f9c-1a2b-4c8d-9e5f-6b7a8c9d0e1f"
PRODUCT_1 = "550e8400-e29b-41d4-a716-446655440000"
PRODUCT_2 = "550e8400-e29b-41d4-a716-446655440001"
ADDRESS_ID = "a0000000-0000-4000-8000-000000000001"
PAYMENT_METHOD_ID = "b0000000-0000-4000-8000-000000000002"

RESERVE_PATH = "/api/v1/inventory/reserve"


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


def make_address(address_id: str = ADDRESS_ID) -> Address:
    return Address(
        id=address_id,
        country="Россия",
        region="Свердловская область",
        city="Екатеринбург",
        street="Мира",
        building="19",
        apartment="42",
        postal_code="620000",
        recipient_name="Иван Иванов",
        recipient_phone="+79990001122",
        is_default=True,
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )


def order_payload(items: list[dict], *, comment: str | None = None) -> dict:
    body: dict = {
        "idempotency_key": str(uuid4()),
        "items": items,
        "address_id": ADDRESS_ID,
        "payment_method_id": PAYMENT_METHOD_ID,
    }
    if comment is not None:
        body["comment"] = comment
    return body


def sku_payload(
    sku_id: str,
    product_id: str,
    *,
    name: str,
    title: str,
    price: int,
    active_quantity: int = 10,
    status: str = "MODERATED",
) -> dict:
    sku = {"id": sku_id, "name": name, "price": price, "active_quantity": active_quantity}
    product = {"id": product_id, "title": title, "status": status, "skus": [sku]}
    return {"product": product, "sku": sku}


def make_handler(
    skus: dict[str, dict],
    reserve: Callable[[httpx.Request], httpx.Response],
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path.startswith("/api/v1/skus/"):
            assert request.headers.get("X-Service-Key") == "test-service-key"
            sku_id = request.url.path.rsplit("/", 1)[-1]
            payload = skus.get(sku_id)
            if payload is None:
                return httpx.Response(404, json={"code": "NOT_FOUND", "message": "SKU not found"})
            return httpx.Response(200, json=payload)
        if request.method == "POST" and request.url.path == RESERVE_PATH:
            return reserve(request)
        raise AssertionError(f"unexpected upstream call {request.method} {request.url.path}")

    return handler


def _reserve_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"reserved": True, "status": "RESERVED"})


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


def _reserve_count(b2b_recorder) -> int:
    return sum(
        1 for r in b2b_recorder.requests if r.method == "POST" and r.url.path == RESERVE_PATH
    )


# ---------------------------------------------------------------------------
# Happy path: checkout reserves all SKUs, resolves the address from the DB,
# creates a PAID order matching the OrderResponse contract (buyer_id, subtotal,
# total, address object, OrderItem.name) and fixes unit_price per item.
# ---------------------------------------------------------------------------
async def test_checkout_creates_paid_order_with_fixed_prices(client, b2b_recorder, address_repository):
    address_repository.add(USER_ID, make_address())
    skus = {
        SKU_1: sku_payload(SKU_1, PRODUCT_1, name="256GB Black", title="iPhone 15", price=12999000),
        SKU_2: sku_payload(SKU_2, PRODUCT_2, name="Silicone Case", title="Case", price=2990000),
    }
    b2b_recorder.set_handler(make_handler(skus, _reserve_ok))

    payload = order_payload(
        [{"sku_id": SKU_1, "quantity": 2}, {"sku_id": SKU_2, "quantity": 1}],
        comment="позвонить за час",
    )

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "PAID"
    assert body["buyer_id"] == USER_ID
    assert "total_amount" not in body
    assert "delivery_address" not in body

    first, second = body["items"]
    assert first["sku_id"] == SKU_1
    assert first["product_id"] == PRODUCT_1
    assert first["name"] == "iPhone 15 – 256GB Black"
    assert "product_title" not in first
    assert "sku_name" not in first
    assert first["unit_price"] == 12999000
    assert first["line_total"] == 12999000 * 2
    assert second["name"] == "Case – Silicone Case"
    assert second["line_total"] == 2990000

    assert body["subtotal"] == 12999000 * 2 + 2990000
    assert body["total"] == body["subtotal"]

    assert body["address"]["id"] == ADDRESS_ID
    assert body["address"]["city"] == "Екатеринбург"
    assert body["address"]["building"] == "19"
    assert body["comment"] == "позвонить за час"
    assert _reserve_count(b2b_recorder) == 1


# ---------------------------------------------------------------------------
# Unhappy: B2B reserve rejects the batch (all-or-nothing) -> 409 RESERVE_FAILED
# carrying failed_items; no order is created.
# ---------------------------------------------------------------------------
async def test_partial_reserve_failure_returns_409(client, b2b_recorder, address_repository):
    address_repository.add(USER_ID, make_address())
    skus = {
        SKU_1: sku_payload(SKU_1, PRODUCT_1, name="256GB Black", title="iPhone 15", price=12999000),
        SKU_2: sku_payload(SKU_2, PRODUCT_2, name="Silicone Case", title="Case", price=2990000),
    }

    def reserve_conflict(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "reserved": False,
                "failed_items": [
                    {"sku_id": SKU_2, "requested": 1, "available": 0, "reason": "INSUFFICIENT_STOCK"}
                ],
            },
        )

    b2b_recorder.set_handler(make_handler(skus, reserve_conflict))

    payload = order_payload([{"sku_id": SKU_1, "quantity": 1}, {"sku_id": SKU_2, "quantity": 1}])

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 409
    body = response.json()
    _assert_error_contract(body, expected_code="RESERVE_FAILED")
    assert body["failed_items"] == [
        {"sku_id": SKU_2, "requested": 1, "available": 0, "reason": "INSUFFICIENT_STOCK"}
    ]


# ---------------------------------------------------------------------------
# Idempotency: a repeated POST with the same idempotency_key returns the
# already-created order (200, not 201) without calling B2B reserve again.
# ---------------------------------------------------------------------------
async def test_idempotency_returns_existing_order(client, b2b_recorder, address_repository):
    address_repository.add(USER_ID, make_address())
    skus = {
        SKU_1: sku_payload(SKU_1, PRODUCT_1, name="256GB Black", title="iPhone 15", price=12999000),
    }
    b2b_recorder.set_handler(make_handler(skus, _reserve_ok))

    payload = order_payload([{"sku_id": SKU_1, "quantity": 1}])

    async with client as ac:
        first = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())
        second = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert _reserve_count(b2b_recorder) == 1


# ---------------------------------------------------------------------------
# Unhappy: B2B is unreachable during checkout -> 503 B2B_UNAVAILABLE.
# ---------------------------------------------------------------------------
async def test_b2b_unavailable_returns_503(client, b2b_recorder, address_repository):
    address_repository.add(USER_ID, make_address())

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unreachable", request=request)

    b2b_recorder.set_handler(handler)

    payload = order_payload([{"sku_id": SKU_1, "quantity": 1}])

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 503
    _assert_error_contract(response.json(), expected_code="B2B_UNAVAILABLE")


# ---------------------------------------------------------------------------
# Edge case: address_id that does not belong to the buyer -> 400
# ADDRESS_NOT_FOUND, B2B never called.
# ---------------------------------------------------------------------------
async def test_unknown_address_returns_400(client, b2b_recorder, address_repository):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called when the address is unknown")

    b2b_recorder.set_handler(handler)

    payload = order_payload([{"sku_id": SKU_1, "quantity": 1}])

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="ADDRESS_NOT_FOUND")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: missing address_id -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_missing_address_id_returns_400(client, b2b_recorder, address_repository):
    address_repository.add(USER_ID, make_address())

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called without address_id")

    b2b_recorder.set_handler(handler)

    payload = {
        "idempotency_key": str(uuid4()),
        "items": [{"sku_id": SKU_1, "quantity": 1}],
        "payment_method_id": PAYMENT_METHOD_ID,
    }

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: empty items -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_empty_items_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for an empty cart")

    b2b_recorder.set_handler(handler)

    payload = {
        "idempotency_key": str(uuid4()),
        "items": [],
        "address_id": ADDRESS_ID,
        "payment_method_id": PAYMENT_METHOD_ID,
    }

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: missing idempotency_key -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_missing_idempotency_key_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called without an idempotency key")

    b2b_recorder.set_handler(handler)

    payload = {
        "items": [{"sku_id": SKU_1, "quantity": 1}],
        "address_id": ADDRESS_ID,
        "payment_method_id": PAYMENT_METHOD_ID,
    }

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: quantity below 1 -> 422 INVALID_QUANTITY, B2B never called.
# ---------------------------------------------------------------------------
async def test_quantity_below_one_returns_422(client, b2b_recorder, address_repository):
    address_repository.add(USER_ID, make_address())

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid quantity")

    b2b_recorder.set_handler(handler)

    payload = order_payload([{"sku_id": SKU_1, "quantity": 0}])

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload, headers=auth_headers())

    assert response.status_code == 422
    _assert_error_contract(response.json(), expected_code="INVALID_QUANTITY")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: no Authorization header -> 401 UNAUTHORIZED.
# ---------------------------------------------------------------------------
async def test_unauthorized_returns_401(client, b2b_recorder):
    payload = order_payload([{"sku_id": SKU_1, "quantity": 1}])

    async with client as ac:
        response = await ac.post("/api/v1/orders", json=payload)

    assert response.status_code == 401
    _assert_error_contract(response.json(), expected_code="UNAUTHORIZED")
