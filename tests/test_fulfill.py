from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import jwt
import pytest

from app.addresses import Address
from app.orders import Order, OrderItem, OrderStatus, retry_pending_fulfillments

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
SKU_1 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
PRODUCT_1 = "550e8400-e29b-41d4-a716-446655440000"
ADDRESS_ID = "a0000000-0000-4000-8000-000000000001"
PAYMENT_METHOD_ID = "b0000000-0000-4000-8000-000000000002"

ADMIN_HEADER = {"X-Service-Key": "dev-service-key"}


def auth_headers(user_id: str = USER_ID) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {"sub": user_id, "role": "buyer", "iat": now, "exp": now + 3600, "jti": str(uuid4())},
        "dev-jwt-secret-for-tests-32-bytes",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _make_order(
    *,
    user_id: str = USER_ID,
    status: OrderStatus = OrderStatus.DELIVERING,
    unit_price: int = 12999000,
    quantity: int = 2,
) -> Order:
    moment = datetime(2026, 4, 16, 10, 30, tzinfo=timezone.utc)
    item = OrderItem(
        id=str(uuid4()),
        sku_id=SKU_1,
        product_id=PRODUCT_1,
        product_title="z",
        sku_name="zz",
        quantity=quantity,
        unit_price=unit_price,
        line_total=unit_price * quantity,
    )
    address = Address(
        id=ADDRESS_ID,
        country="Россия",
        city="Екб",
        street="Тургенева",
        building="4",
        is_default=True,
        created_at=moment,
    )
    return Order(
        id=str(uuid4()),
        user_id=user_id,
        status=status,
        items=(item,),
        total_amount=item.line_total,
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


def _fulfill_handler(order_id: str):
    def handler(request: httpx.Request) -> httpx.Response:
        if "fulfill" in request.url.path:
            return httpx.Response(
                200,
                json={"order_id": order_id, "status": "FULFILLED", "processed_at": "2026-04-16T12:00:00Z"},
            )
        return httpx.Response(500, json={"message": "unexpected call"})
    return handler

async def test_delivered_status_triggers_fulfill_to_b2b(client, b2b_recorder, order_repository):
    order = _make_order(status=OrderStatus.DELIVERING, quantity=2)
    await order_repository.create(order)

    fulfill_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "fulfill" in request.url.path:
            fulfill_calls.append(request)
            return httpx.Response(
                200,
                json={"order_id": order.id, "status": "FULFILLED", "processed_at": "2026-04-16T12:00:00Z"},
            )
        return httpx.Response(500)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        resp = await ac.post(f"/api/v1/orders/{order.id}/deliver", headers=ADMIN_HEADER)

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "DELIVERED"

    assert len(fulfill_calls) == 1
    sent = json.loads(fulfill_calls[0].content)
    assert sent["order_id"] == order.id
    assert sent["items"] == [{"sku_id": SKU_1, "quantity": 2}]


async def test_fulfill_failure_retried_asynchronously(client, b2b_recorder, order_repository, caplog):
    order = _make_order(status=OrderStatus.DELIVERING)
    await order_repository.create(order)

    def handler(request: httpx.Request) -> httpx.Response:
        if "fulfill" in request.url.path:
            return httpx.Response(503, json={"message": "B2B down"})
        return httpx.Response(500)

    b2b_recorder.set_handler(handler)

    with caplog.at_level(logging.WARNING, logger="app.orders"):
        async with client as ac:
            resp = await ac.post(f"/api/v1/orders/{order.id}/deliver", headers=ADMIN_HEADER)

    assert resp.status_code == 200
    assert resp.json()["status"] == "DELIVERED"

    warning_messages = [r.message for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("fulfill" in m for m in warning_messages), (
        f"Expected 'fulfill' warning in logs, got: {warning_messages}"
    )


async def test_repeated_fulfill_idempotent(client, b2b_recorder, order_repository):
    order = _make_order(status=OrderStatus.DELIVERING)
    await order_repository.create(order)

    fulfill_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "fulfill" in request.url.path:
            fulfill_calls.append(request)
            return httpx.Response(
                200,
                json={"order_id": order.id, "status": "FULFILLED", "processed_at": "2026-04-16T12:00:00Z"},
            )
        return httpx.Response(500)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        first = await ac.post(f"/api/v1/orders/{order.id}/deliver", headers=ADMIN_HEADER)
        second = await ac.post(f"/api/v1/orders/{order.id}/deliver", headers=ADMIN_HEADER)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["status"] == "DELIVERED"
    assert second.json()["status"] == "DELIVERED"
    assert len(fulfill_calls) == 2

async def test_deliver_missing_service_key_returns_401(client, order_repository):
    order = _make_order(status=OrderStatus.DELIVERING)
    await order_repository.create(order)

    async with client as ac:
        resp = await ac.post(f"/api/v1/orders/{order.id}/deliver")

    assert resp.status_code == 401
    _assert_error_contract(resp.json(), expected_code="UNAUTHORIZED")


async def test_deliver_wrong_service_key_returns_401(client, order_repository):
    order = _make_order(status=OrderStatus.DELIVERING)
    await order_repository.create(order)

    async with client as ac:
        resp = await ac.post(
            f"/api/v1/orders/{order.id}/deliver",
            headers={"X-Service-Key": "wrong"},
        )

    assert resp.status_code == 401


async def test_deliver_order_not_found_returns_404(client, b2b_recorder):
    b2b_recorder.set_handler(_fulfill_handler(str(uuid4())))

    async with client as ac:
        resp = await ac.post(f"/api/v1/orders/{uuid4()}/deliver", headers=ADMIN_HEADER)

    assert resp.status_code == 404
    _assert_error_contract(resp.json(), expected_code="ORDER_NOT_FOUND")


async def test_deliver_paid_order_returns_409(client, order_repository, b2b_recorder):
    order = _make_order(status=OrderStatus.PAID)
    await order_repository.create(order)

    async with client as ac:
        resp = await ac.post(f"/api/v1/orders/{order.id}/deliver", headers=ADMIN_HEADER)

    assert resp.status_code == 409
    _assert_error_contract(resp.json(), expected_code="DELIVER_NOT_ALLOWED")


async def test_retry_pending_fulfillments_calls_b2b(b2b_recorder, order_repository):
    from app.b2b_client import B2BClient

    order = _make_order(status=OrderStatus.DELIVERED)
    await order_repository.create(order)

    fulfill_calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if "fulfill" in request.url.path:
            fulfill_calls.append(request)
            return httpx.Response(
                200,
                json={"order_id": order.id, "status": "FULFILLED", "processed_at": "2026-04-16T12:00:00Z"},
            )
        return httpx.Response(500)

    b2b_recorder.set_handler(handler)
    b2b = B2BClient(base_url="http://b2b.test", service_key="test-key", transport=b2b_recorder.transport)

    try:
        count = await retry_pending_fulfillments(order_repository, b2b)
    finally:
        await b2b.aclose()

    assert count == 1
    assert len(fulfill_calls) == 1
    sent = json.loads(fulfill_calls[0].content)
    assert sent["order_id"] == order.id


async def test_buyer_cannot_trigger_deliver(client, order_repository):
    order = _make_order(status=OrderStatus.DELIVERING)
    await order_repository.create(order)

    async with client as ac:
        resp = await ac.post(
            f"/api/v1/orders/{order.id}/deliver",
            headers=auth_headers(),
        )

    assert resp.status_code == 401
