from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import user_id_from_jwt
from app.config import settings
from app.errors import InvalidRequest, MissingIdempotencyKey, Unauthorized
from app.orders import OrderLine, OrderService, OrderStatus, to_order_response, to_order_short_response
from app.query_parsing import validate_pagination, validate_uuid

router = APIRouter()


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


@router.get("/api/v1/orders")
async def list_orders(request: Request) -> JSONResponse:
    user_id = user_id_from_jwt(request)
    limit_raw = _int_query(request, "limit")
    offset_raw = _int_query(request, "offset")
    limit, offset = validate_pagination(limit_raw, offset_raw)
    status = _parse_order_status(request.query_params.get("status"))

    service = get_order_service(request)
    orders, total = await service.list_orders(user_id, limit, offset, status)
    return JSONResponse(
        status_code=200,
        content={
            "items": [to_order_short_response(o) for o in orders],
            "total_count": total,
            "limit": limit,
            "offset": offset,
        },
    )


@router.get("/api/v1/orders/{order_id}")
async def get_order(request: Request, order_id: str) -> JSONResponse:
    user_id = user_id_from_jwt(request)
    if validate_uuid(order_id, field="order_id") is None:
        raise InvalidRequest("order_id must be a valid UUID")

    service = get_order_service(request)
    order = await service.get_order(user_id, order_id)
    return JSONResponse(status_code=200, content=to_order_response(order))


@router.post("/api/v1/orders")
async def create_order(request: Request) -> JSONResponse:
    user_id = user_id_from_jwt(request)
    body = await _json_body(request)

    idempotency_key = _idempotency_key(request, body)
    address_id = _required_uuid(body, "address_id")
    payment_method_id = _required_uuid(body, "payment_method_id")
    comment = _comment(body)
    items_snapshot = _items_snapshot(body)

    service = get_order_service(request)
    order, created = await service.create_order(
        user_id, idempotency_key, address_id, payment_method_id, comment, items_snapshot
    )
    return JSONResponse(status_code=201 if created else 200, content=to_order_response(order))


@router.post("/api/v1/orders/{order_id}/deliver")
async def deliver_order(request: Request, order_id: str) -> JSONResponse:
    _require_service_key(request)
    if validate_uuid(order_id, field="order_id") is None:
        raise InvalidRequest("order_id must be a valid UUID")

    service = get_order_service(request)
    order = await service.deliver_order(order_id)
    return JSONResponse(status_code=200, content=to_order_response(order))


@router.post("/api/v1/orders/{order_id}/cancel")
async def cancel_order(request: Request, order_id: str) -> JSONResponse:
    user_id = user_id_from_jwt(request)
    validate_uuid(order_id, field="id")

    service = get_order_service(request)
    order = await service.cancel_order(user_id, order_id)
    return JSONResponse(status_code=200, content=to_order_response(order))


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise InvalidRequest("Request body must be valid JSON")
    if not isinstance(body, dict):
        raise InvalidRequest("Request body must be an object")
    return body


def _idempotency_key(request: Request, body: dict) -> str:
    raw = request.headers.get("Idempotency-Key") or body.get("idempotency_key")
    if not isinstance(raw, str) or not raw:
        raise MissingIdempotencyKey()
    valid = validate_uuid(raw, field="idempotency_key")
    if valid is None:
        raise MissingIdempotencyKey("idempotency_key должен быть UUID")
    return valid


def _items_snapshot(body: dict) -> list[OrderLine] | None:
    raw_items = body.get("items_snapshot")
    if raw_items is None:
        return None
    if not isinstance(raw_items, list):
        raise InvalidRequest("items_snapshot должен быть массивом")
    lines: list[OrderLine] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise InvalidRequest("Каждая позиция items_snapshot должна быть объектом")
        sku_id_raw = raw.get("sku_id")
        if not isinstance(sku_id_raw, str) or validate_uuid(sku_id_raw, field="sku_id") is None:
            raise InvalidRequest("sku_id должен быть UUID")
        quantity = raw.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 1:
            raise InvalidRequest("quantity должен быть положительным целым")
        lines.append(OrderLine(sku_id=sku_id_raw, quantity=quantity))
    return lines


def _required_uuid(body: dict, field: str) -> str:
    raw = body.get(field)
    if not isinstance(raw, str) or not raw:
        raise InvalidRequest(f"{field} обязателен")
    valid = validate_uuid(raw, field=field)
    if valid is None:
        raise InvalidRequest(f"{field} должен быть UUID")
    return valid


def _comment(body: dict) -> str | None:
    value = body.get("comment")
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidRequest("comment должен быть строкой")
    return value


def _require_service_key(request: Request) -> None:
    key = request.headers.get("X-Service-Key")
    if not key or key != settings.b2b_service_key:
        raise Unauthorized("Invalid or missing X-Service-Key")


def _int_query(request: Request, name: str) -> int | None:
    raw = request.query_params.get(name)
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        raise InvalidRequest(f"{name} must be an integer")


def _parse_order_status(raw: str | None) -> OrderStatus | None:
    if raw is None:
        return None
    try:
        return OrderStatus(raw)
    except ValueError:
        raise InvalidRequest(
            f"Invalid status: {raw!r}. Allowed: "
            + ", ".join(s.value for s in OrderStatus)
        )
