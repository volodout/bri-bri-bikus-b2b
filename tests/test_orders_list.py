from __future__ import annotations

import time
from datetime import datetime, timezone
from uuid import uuid4

import jwt

from app.addresses import Address
from app.orders import Order, OrderItem, OrderStatus

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_USER_ID = "223e4567-e89b-12d3-a456-426614174999"
SKU_1 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
PRODUCT_1 = "550e8400-e29b-41d4-a716-446655440000"
ADDRESS_ID = "a0000000-0000-4000-8000-000000000001"
PAYMENT_METHOD_ID = "b0000000-0000-4000-8000-000000000002"


def auth_headers(user_id: str = USER_ID) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {"sub": user_id, "role": "buyer", "iat": now, "exp": now + 3600, "jti": str(uuid4())},
        "dev-jwt-secret-for-tests-32-bytes",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _make_address() -> Address:
    return Address(
        id=ADDRESS_ID,
        country="Россия",
        city="Екатеринбург",
        street="Мира",
        building="19",
        is_default=True,
        created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
    )


def _make_order(
    *,
    order_id: str | None = None,
    user_id: str = USER_ID,
    status: OrderStatus = OrderStatus.PAID,
    unit_price: int = 12999000,
    quantity: int = 2,
    created_at: datetime | None = None,
) -> Order:
    moment = created_at or datetime(2026, 4, 16, 10, 30, tzinfo=timezone.utc)
    item = OrderItem(
        id=str(uuid4()),
        sku_id=SKU_1,
        product_id=PRODUCT_1,
        product_title="iPhone 15 Pro Max",
        sku_name="256GB Black",
        quantity=quantity,
        unit_price=unit_price,
        line_total=unit_price * quantity,
    )
    return Order(
        id=order_id or str(uuid4()),
        user_id=user_id,
        status=status,
        items=(item,),
        total_amount=item.line_total,
        address=_make_address(),
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

async def test_orders_list_returns_own_orders_paginated(client, order_repository):
    order_a = _make_order(
        created_at=datetime(2026, 4, 16, 10, 0, tzinfo=timezone.utc),
    )
    order_b = _make_order(
        created_at=datetime(2026, 4, 17, 10, 0, tzinfo=timezone.utc),
        status=OrderStatus.DELIVERED,
    )
    other_order = _make_order(user_id=OTHER_USER_ID)
    await order_repository.create(order_a)
    await order_repository.create(order_b)
    await order_repository.create(other_order)

    async with client as ac:
        response = await ac.get("/api/v1/orders", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()

    assert body["total_count"] == 2
    assert body["limit"] == 20
    assert body["offset"] == 0

    ids = {item["id"] for item in body["items"]}
    assert order_a.id in ids
    assert order_b.id in ids
    assert other_order.id not in ids

    for item in body["items"]:
        assert "items_count" in item
        assert "items" not in item
        assert item["items_count"] == 1
        assert "total_amount" in item
        assert "status" in item

    # Newest first
    assert body["items"][0]["id"] == order_b.id
    assert body["items"][1]["id"] == order_a.id


async def test_orders_list_pagination_offset(client, order_repository):
    for i in range(3):
        await order_repository.create(
            _make_order(created_at=datetime(2026, 4, i + 1, 10, 0, tzinfo=timezone.utc))
        )

    async with client as ac:
        page1 = await ac.get("/api/v1/orders?limit=2&offset=0", headers=auth_headers())
        page2 = await ac.get("/api/v1/orders?limit=2&offset=2", headers=auth_headers())

    assert page1.status_code == 200
    assert page2.status_code == 200
    b1 = page1.json()
    b2 = page2.json()

    assert b1["total_count"] == 3
    assert len(b1["items"]) == 2
    assert b2["total_count"] == 3
    assert len(b2["items"]) == 1

    ids1 = {item["id"] for item in b1["items"]}
    ids2 = {item["id"] for item in b2["items"]}
    assert not ids1.intersection(ids2)


async def test_orders_list_status_filter(client, order_repository):
    paid_order = _make_order(status=OrderStatus.PAID)
    delivered_order = _make_order(status=OrderStatus.DELIVERED)
    await order_repository.create(paid_order)
    await order_repository.create(delivered_order)

    async with client as ac:
        response = await ac.get("/api/v1/orders?status=PAID", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == paid_order.id
    assert body["items"][0]["status"] == "PAID"


async def test_orders_list_empty_for_new_user(client):
    async with client as ac:
        response = await ac.get("/api/v1/orders", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_count"] == 0


async def test_orders_list_requires_auth(client):
    async with client as ac:
        response = await ac.get("/api/v1/orders")

    assert response.status_code == 401
    _assert_error_contract(response.json(), expected_code="UNAUTHORIZED")


async def test_order_detail_shows_fixed_prices(client, order_repository):
    price_at_purchase = 12999000
    order = _make_order(unit_price=price_at_purchase, quantity=2)
    await order_repository.create(order)

    async with client as ac:
        response = await ac.get(f"/api/v1/orders/{order.id}", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()

    assert body["id"] == order.id
    assert body["status"] == "PAID"

    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["unit_price"] == price_at_purchase
    assert item["line_total"] == price_at_purchase * 2
    assert item["sku_id"] == SKU_1
    assert item["quantity"] == 2

    assert body["subtotal"] == price_at_purchase * 2


async def test_order_detail_contains_item_id(client, order_repository):
    order = _make_order()
    await order_repository.create(order)

    async with client as ac:
        response = await ac.get(f"/api/v1/orders/{order.id}", headers=auth_headers())

    assert response.status_code == 200
    item = response.json()["items"][0]
    assert "id" in item


async def test_other_user_order_returns_404_not_403(client, order_repository):
    order = _make_order(user_id=USER_ID)
    await order_repository.create(order)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/orders/{order.id}", headers=auth_headers(OTHER_USER_ID)
        )

    assert response.status_code == 404
    body = response.json()
    _assert_error_contract(body, expected_code="ORDER_NOT_FOUND")


async def test_order_detail_nonexistent_returns_404(client):
    nonexistent_id = str(uuid4())

    async with client as ac:
        response = await ac.get(f"/api/v1/orders/{nonexistent_id}", headers=auth_headers())

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="ORDER_NOT_FOUND")


async def test_order_detail_requires_auth(client, order_repository):
    order = _make_order()
    await order_repository.create(order)

    async with client as ac:
        response = await ac.get(f"/api/v1/orders/{order.id}")

    assert response.status_code == 401
    _assert_error_contract(response.json(), expected_code="UNAUTHORIZED")


async def test_orders_list_invalid_status_returns_400(client):
    async with client as ac:
        response = await ac.get("/api/v1/orders?status=UNKNOWN", headers=auth_headers())

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
