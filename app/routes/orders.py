from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.auth import user_id_from_jwt
from app.errors import EmptyOrderItems, InvalidOrderQuantity, InvalidRequest, MissingIdempotencyKey
from app.orders import OrderLine, OrderService, to_order_response
from app.query_parsing import validate_uuid

router = APIRouter()


def get_order_service(request: Request) -> OrderService:
    return request.app.state.order_service


@router.post("/api/v1/orders")
async def create_order(request: Request) -> JSONResponse:
    user_id = user_id_from_jwt(request)
    body = await _json_body(request)

    idempotency_key = _idempotency_key(request, body)
    lines = _order_lines(body)
    address_id = _required_uuid(body, "address_id")
    payment_method_id = _required_uuid(body, "payment_method_id")
    comment = _comment(body)

    service = get_order_service(request)
    order, created = await service.create_order(
        user_id, idempotency_key, lines, address_id, payment_method_id, comment
    )
    return JSONResponse(status_code=201 if created else 200, content=to_order_response(order))


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


def _order_lines(body: dict) -> list[OrderLine]:
    raw_items = body.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise EmptyOrderItems()
    lines: list[OrderLine] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            raise InvalidRequest("Каждая позиция должна быть объектом")
        sku_id_raw = raw.get("sku_id")
        if not isinstance(sku_id_raw, str):
            raise InvalidRequest("sku_id должен быть UUID")
        sku_id = validate_uuid(sku_id_raw, field="sku_id")
        if sku_id is None:
            raise InvalidRequest("sku_id должен быть UUID")
        quantity = raw.get("quantity")
        if not isinstance(quantity, int) or isinstance(quantity, bool):
            raise InvalidOrderQuantity()
        lines.append(OrderLine(sku_id=sku_id, quantity=quantity))
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
