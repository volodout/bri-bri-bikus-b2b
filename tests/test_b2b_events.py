from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import jwt

from app.cart import CartIdentity
from app.orders import Order, OrderItem, OrderStatus
from app.addresses import Address

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
SKU_1 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
SKU_2 = "8a4e3f9c-1234-40de-944b-e07fc1f90ae8"
PRODUCT_1 = "550e8400-e29b-41d4-a716-446655440000"
ADDRESS_ID = "a0000000-0000-4000-8000-000000000001"
PAYMENT_METHOD_ID = "b0000000-0000-4000-8000-000000000002"

SERVICE_KEY_HEADER = {"X-Service-Key": "dev-service-key"}
OCCURRED_AT = "2026-04-16T12:00:00Z"


def auth_headers(user_id: str = USER_ID) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {"sub": user_id, "role": "buyer", "iat": now, "exp": now + 3600, "jti": str(uuid4())},
        "dev-jwt-secret-for-tests-32-bytes",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _product_blocked_event(product_id: str = PRODUCT_1, key: str | None = None) -> dict:
    return {
        "event_type": "PRODUCT_BLOCKED",
        "idempotency_key": key or str(uuid4()),
        "occurred_at": OCCURRED_AT,
        "payload": {"product_id": product_id, "reason": "Описание не соответствует"},
    }


def _sku_out_of_stock_event(sku_id: str = SKU_1, key: str | None = None) -> dict:
    return {
        "event_type": "SKU_OUT_OF_STOCK",
        "idempotency_key": key or str(uuid4()),
        "occurred_at": OCCURRED_AT,
        "payload": {"sku_id": sku_id, "product_id": PRODUCT_1, "available_quantity": 0},
    }


def _make_order(
    *,
    user_id: str = USER_ID,
    status: OrderStatus = OrderStatus.PAID,
    unit_price: int = 12999000,
) -> Order:
    moment = datetime(2026, 4, 16, 10, 30, tzinfo=timezone.utc)
    item = OrderItem(
        id=str(uuid4()),
        sku_id=SKU_1,
        product_id=PRODUCT_1,
        product_title="iPhone 15 Pro Max",
        sku_name="256GB Black",
        quantity=1,
        unit_price=unit_price,
        line_total=unit_price,
    )
    address = Address(
        id=ADDRESS_ID,
        country="Россия",
        city="Москва",
        street="Арбат",
        building="1",
        is_default=True,
        created_at=moment,
    )
    return Order(
        id=str(uuid4()),
        user_id=user_id,
        status=status,
        items=(item,),
        total_amount=unit_price,
        address=address,
        payment_method_id=PAYMENT_METHOD_ID,
        comment=None,
        idempotency_key=str(uuid4()),
        created_at=moment,
        updated_at=moment,
    )


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


# ── DoD tests ──────────────────────────────────────────────────────────────────

async def test_product_blocked_marks_cart_items_unavailable(client, cart_repository):
    identity = CartIdentity(user_id=USER_ID, session_id=None)
    await cart_repository.add(identity, product_id=PRODUCT_1, sku_id=SKU_1, quantity=2)

    async with client as ac:
        event_resp = await ac.post(
            "/api/v1/b2b/events",
            headers=SERVICE_KEY_HEADER,
            json=_product_blocked_event(),
        )
        assert event_resp.status_code == 202
        assert event_resp.json() == {"accepted": True}

        cart_resp = await ac.get("/api/v1/cart", headers=auth_headers())

    assert cart_resp.status_code == 200
    cart = cart_resp.json()
    assert len(cart["items"]) == 1
    item = cart["items"][0]
    assert item["is_available"] is False
    assert item["unavailable_reason"] == "PRODUCT_BLOCKED"


async def test_orders_not_affected_by_product_blocked(client, order_repository):
    order = _make_order(unit_price=12999000)
    await order_repository.create(order)

    async with client as ac:
        await ac.post(
            "/api/v1/b2b/events",
            headers=SERVICE_KEY_HEADER,
            json=_product_blocked_event(),
        )
        order_resp = await ac.get(f"/api/v1/orders/{order.id}", headers=auth_headers())

    assert order_resp.status_code == 200
    body = order_resp.json()
    assert body["status"] == "PAID"
    assert body["items"][0]["unit_price"] == 12999000
    assert body["items"][0]["sku_id"] == SKU_1


async def test_idempotent_event_no_side_effects(client, cart_repository):
    identity = CartIdentity(user_id=USER_ID, session_id=None)
    await cart_repository.add(identity, product_id=PRODUCT_1, sku_id=SKU_1, quantity=1)

    key = str(uuid4())
    event = _product_blocked_event(key=key)

    async with client as ac:
        first = await ac.post("/api/v1/b2b/events", headers=SERVICE_KEY_HEADER, json=event)
        second = await ac.post("/api/v1/b2b/events", headers=SERVICE_KEY_HEADER, json=event)

    assert first.status_code == 202
    # Spec: 409 for duplicate (diverges from canon flow which says 200; spec wins)
    assert second.status_code == 409
    _assert_error_contract(second.json(), expected_code="DUPLICATE_EVENT")

    # No double-processing: state is consistent (item still has exactly one block reason)
    item = list(cart_repository._items.values())[0]
    assert item.unavailable_reason == "PRODUCT_BLOCKED"


async def test_missing_service_key_returns_401(client):
    async with client as ac:
        resp = await ac.post(
            "/api/v1/b2b/events",
            json=_product_blocked_event(),
        )

    assert resp.status_code == 401
    _assert_error_contract(resp.json(), expected_code="UNAUTHORIZED")


# ── Additional coverage ────────────────────────────────────────────────────────

async def test_wrong_service_key_returns_401(client):
    async with client as ac:
        resp = await ac.post(
            "/api/v1/b2b/events",
            headers={"X-Service-Key": "wrong-key"},
            json=_product_blocked_event(),
        )

    assert resp.status_code == 401
    _assert_error_contract(resp.json(), expected_code="UNAUTHORIZED")


async def test_sku_out_of_stock_marks_sku_unavailable(client, cart_repository):
    identity = CartIdentity(user_id=USER_ID, session_id=None)
    await cart_repository.add(identity, product_id=PRODUCT_1, sku_id=SKU_1, quantity=1)

    async with client as ac:
        resp = await ac.post(
            "/api/v1/b2b/events",
            headers=SERVICE_KEY_HEADER,
            json=_sku_out_of_stock_event(sku_id=SKU_1),
        )
        assert resp.status_code == 202

        cart_resp = await ac.get("/api/v1/cart", headers=auth_headers())

    assert cart_resp.status_code == 200
    item = cart_resp.json()["items"][0]
    assert item["is_available"] is False
    assert item["unavailable_reason"] == "OUT_OF_STOCK"


async def test_product_deleted_marks_cart_items_unavailable(client, cart_repository):
    identity = CartIdentity(user_id=USER_ID, session_id=None)
    await cart_repository.add(identity, product_id=PRODUCT_1, sku_id=SKU_1, quantity=1)

    event = {
        "event_type": "PRODUCT_DELETED",
        "idempotency_key": str(uuid4()),
        "occurred_at": OCCURRED_AT,
        "payload": {"product_id": PRODUCT_1},
    }
    async with client as ac:
        resp = await ac.post("/api/v1/b2b/events", headers=SERVICE_KEY_HEADER, json=event)
        assert resp.status_code == 202

        cart_resp = await ac.get("/api/v1/cart", headers=auth_headers())

    item = cart_resp.json()["items"][0]
    assert item["is_available"] is False
    assert item["unavailable_reason"] == "PRODUCT_DELETED"


async def test_unknown_event_type_returns_400(client):
    async with client as ac:
        resp = await ac.post(
            "/api/v1/b2b/events",
            headers=SERVICE_KEY_HEADER,
            json={
                "event_type": "COMPLETELY_UNKNOWN",
                "idempotency_key": str(uuid4()),
                "occurred_at": OCCURRED_AT,
                "payload": {},
            },
        )

    assert resp.status_code == 400
    _assert_error_contract(resp.json(), expected_code="INVALID_REQUEST")


async def test_missing_idempotency_key_returns_400(client):
    async with client as ac:
        resp = await ac.post(
            "/api/v1/b2b/events",
            headers=SERVICE_KEY_HEADER,
            json={
                "event_type": "PRODUCT_BLOCKED",
                "occurred_at": OCCURRED_AT,
                "payload": {"product_id": PRODUCT_1},
            },
        )

    assert resp.status_code == 400
    _assert_error_contract(resp.json(), expected_code="INVALID_REQUEST")


async def test_price_changed_event_acknowledged_without_cart_effect(client, cart_repository):
    identity = CartIdentity(user_id=USER_ID, session_id=None)
    await cart_repository.add(identity, product_id=PRODUCT_1, sku_id=SKU_1, quantity=1)

    async with client as ac:
        resp = await ac.post(
            "/api/v1/b2b/events",
            headers=SERVICE_KEY_HEADER,
            json={
                "event_type": "PRICE_CHANGED",
                "idempotency_key": str(uuid4()),
                "occurred_at": OCCURRED_AT,
                "payload": {
                    "sku_id": SKU_1,
                    "product_id": PRODUCT_1,
                    "old_price": 12999000,
                    "new_price": 11999000,
                },
            },
        )

    assert resp.status_code == 202
    # Cart items should NOT be marked unavailable for price changes
    item = list(cart_repository._items.values())[0]
    assert item.unavailable_reason is None
