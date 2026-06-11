from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from uuid import uuid4

import httpx
import jwt

from app.addresses import Address
from app.b2b_client import B2BClient
from app.orders import (
    InMemoryOrderRepository,
    Order,
    OrderItem,
    OrderStatus,
    retry_pending_cancellations,
)

USER_ID = "123e4567-e89b-12d3-a456-426614174000"
OTHER_USER_ID = "223e4567-e89b-12d3-a456-426614174999"
ORDER_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
SKU_1 = "7c9e6679-7425-40de-944b-e07fc1f90ae7"
PRODUCT_1 = "550e8400-e29b-41d4-a716-446655440000"
ADDRESS_ID = "a0000000-0000-4000-8000-000000000001"
PAYMENT_METHOD_ID = "b0000000-0000-4000-8000-000000000002"

UNRESERVE_PATH = "/api/v1/inventory/unreserve"


def auth_headers(user_id: str = USER_ID) -> dict[str, str]:
    now = int(time.time())
    token = jwt.encode(
        {"sub": user_id, "role": "buyer", "iat": now, "exp": now + 3600, "jti": str(uuid4())},
        "dev-jwt-secret-for-tests-32-bytes",
        algorithm="HS256",
    )
    return {"Authorization": f"Bearer {token}"}


def _make_order(*, order_id: str = ORDER_ID, user_id: str = USER_ID, status: OrderStatus) -> Order:
    moment = datetime(2026, 4, 16, 10, 30, tzinfo=timezone.utc)
    item = OrderItem(
        id="d4e5f6a7-b8c9-0123-4567-890abcdef012",
        sku_id=SKU_1,
        product_id=PRODUCT_1,
        product_title="iPhone 15 Pro Max",
        sku_name="256GB Black",
        quantity=2,
        unit_price=12999000,
        line_total=25998000,
    )
    address = Address(
        id=ADDRESS_ID,
        country="Россия",
        city="Екатеринбург",
        street="Мира",
        building="19",
        is_default=True,
        created_at=moment,
    )
    return Order(
        id=order_id,
        user_id=user_id,
        status=status,
        items=(item,),
        total_amount=25998000,
        address=address,
        payment_method_id=PAYMENT_METHOD_ID,
        comment=None,
        idempotency_key=str(uuid4()),
        created_at=moment,
        updated_at=moment,
    )


def _unreserve_ok(request: httpx.Request) -> httpx.Response:
    assert request.method == "POST"
    assert request.url.path == UNRESERVE_PATH
    assert request.headers.get("X-Service-Key") == "test-service-key"
    body = json.loads(request.content)
    assert body["order_id"] == ORDER_ID
    assert body["items"] == [{"sku_id": SKU_1, "quantity": 2}]
    return httpx.Response(
        200,
        json={"order_id": ORDER_ID, "status": "UNRESERVED", "processed_at": "2026-04-16T11:00:00Z"},
    )


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


def _unreserve_count(b2b_recorder) -> int:
    return sum(
        1 for r in b2b_recorder.requests if r.method == "POST" and r.url.path == UNRESERVE_PATH
    )


# ---------------------------------------------------------------------------
# Happy path: a PAID order is cancelled. B2C calls B2B unreserve and, on
# success, transitions the order to CANCELLED.
# ---------------------------------------------------------------------------
async def test_cancel_paid_order_transitions_to_cancelled(client, b2b_recorder, order_repository):
    await order_repository.create(_make_order(status=OrderStatus.PAID))
    b2b_recorder.set_handler(_unreserve_ok)

    async with client as ac:
        response = await ac.post(f"/api/v1/orders/{ORDER_ID}/cancel", headers=auth_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ORDER_ID
    assert body["status"] == "CANCELLED"
    assert _unreserve_count(b2b_recorder) == 1

    stored = await order_repository.get_by_id(ORDER_ID, USER_ID)
    assert stored is not None and stored.status is OrderStatus.CANCELLED


# ---------------------------------------------------------------------------
# Unhappy: B2B unreserve is unavailable. The cancel intent is accepted and the
# order moves to CANCEL_PENDING for async retry — never "try again later".
# ---------------------------------------------------------------------------
async def test_unreserve_failure_transitions_to_cancel_pending(
    client, b2b_recorder, order_repository
):
    await order_repository.create(_make_order(status=OrderStatus.PAID))

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unreachable", request=request)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.post(f"/api/v1/orders/{ORDER_ID}/cancel", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "CANCEL_PENDING"
    assert _unreserve_count(b2b_recorder) == 1

    stored = await order_repository.get_by_id(ORDER_ID, USER_ID)
    assert stored is not None and stored.status is OrderStatus.CANCEL_PENDING


# ---------------------------------------------------------------------------
# Spec: an ASSEMBLING order is still cancellable (CREATED/PAID/ASSEMBLING). The
# buyer changed their mind before shipping; B2B unreserve runs and the order
# transitions to CANCELLED.
# ---------------------------------------------------------------------------
async def test_cancel_assembling_order_returns_409(client, b2b_recorder, order_repository):
    await order_repository.create(_make_order(status=OrderStatus.ASSEMBLING))
    b2b_recorder.set_handler(_unreserve_ok)

    async with client as ac:
        response = await ac.post(f"/api/v1/orders/{ORDER_ID}/cancel", headers=auth_headers())

    assert response.status_code == 200
    assert response.json()["status"] == "CANCELLED"
    assert _unreserve_count(b2b_recorder) == 1

    stored = await order_repository.get_by_id(ORDER_ID, USER_ID)
    assert stored is not None and stored.status is OrderStatus.CANCELLED


# ---------------------------------------------------------------------------
# Edge case: an order already shipping can no longer be cancelled -> 409
# CANCEL_NOT_ALLOWED carrying the current status; B2B is never called.
# ---------------------------------------------------------------------------
async def test_cancel_delivering_order_returns_409(client, b2b_recorder, order_repository):
    await order_repository.create(_make_order(status=OrderStatus.DELIVERING))

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for a non-cancellable order")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.post(f"/api/v1/orders/{ORDER_ID}/cancel", headers=auth_headers())

    assert response.status_code == 409
    body = response.json()
    _assert_error_contract(body, expected_code="CANCEL_NOT_ALLOWED")
    assert body["current_status"] == "DELIVERING"
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# IDOR: cancelling another user's order returns 404 ORDER_NOT_FOUND (not 403),
# so order existence is not leaked; B2B is never called.
# ---------------------------------------------------------------------------
async def test_other_user_order_returns_404(client, b2b_recorder, order_repository):
    await order_repository.create(_make_order(user_id=USER_ID, status=OrderStatus.PAID))

    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for someone else's order")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.post(
            f"/api/v1/orders/{ORDER_ID}/cancel", headers=auth_headers(OTHER_USER_ID)
        )

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="ORDER_NOT_FOUND")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Async retry worker: a CANCEL_PENDING order is driven to CANCELLED once B2B
# unreserve succeeds (Celery/cron entry point in app/jobs.py).
# ---------------------------------------------------------------------------
async def test_retry_pending_cancellations_transitions_to_cancelled():
    repository = InMemoryOrderRepository()
    await repository.create(_make_order(status=OrderStatus.CANCEL_PENDING))

    transport = httpx.MockTransport(_unreserve_ok)
    b2b = B2BClient(base_url="http://b2b.test", service_key="test-service-key", transport=transport)

    cancelled = await retry_pending_cancellations(repository, b2b)
    await b2b.aclose()

    assert cancelled == 1
    stored = await repository.get_by_id(ORDER_ID, USER_ID)
    assert stored is not None and stored.status is OrderStatus.CANCELLED
