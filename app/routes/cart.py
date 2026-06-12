from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.cart import CartService, identity_from_request
from app.errors import InvalidRequest
from app.query_parsing import validate_uuid

router = APIRouter()


def get_cart_service(request: Request) -> CartService:
    return request.app.state.cart_service


@router.get("/api/v1/cart")
async def get_cart(request: Request) -> dict:
    identity = identity_from_request(request)
    service = get_cart_service(request)
    return await service.get_cart(identity)


@router.delete("/api/v1/cart", status_code=204)
async def clear_cart(request: Request) -> Response:
    identity = identity_from_request(request)
    service = get_cart_service(request)
    await service.clear(identity)
    return Response(status_code=204)


@router.post("/api/v1/cart/items")
async def add_cart_item(request: Request) -> dict:
    identity = identity_from_request(request)
    body = await _json_body(request)
    sku_id = _require_uuid(body.get("sku_id"), field="sku_id")
    quantity = _quantity_from_body(body)

    service = get_cart_service(request)
    return await service.add_item(identity, sku_id, quantity)


@router.patch("/api/v1/cart/items/{sku_id}")
async def update_cart_item(request: Request, sku_id: str) -> dict:
    identity = identity_from_request(request)
    sku_id = _require_uuid(sku_id, field="sku_id")
    body = await _json_body(request)
    quantity = _quantity_from_body(body)

    service = get_cart_service(request)
    return await service.update_item(identity, sku_id, quantity)


@router.get("/api/v1/cart/items/{item_id}")
async def get_cart_item(request: Request, item_id: str) -> dict:
    identity = identity_from_request(request)
    item_id = _require_uuid(item_id, field="item_id")
    service = get_cart_service(request)
    return await service.get_item(identity, item_id)


@router.delete("/api/v1/cart/items/{sku_id}")
async def delete_cart_item(request: Request, sku_id: str) -> dict:
    identity = identity_from_request(request)
    sku_id = _require_uuid(sku_id, field="sku_id")
    service = get_cart_service(request)
    return await service.remove_item(identity, sku_id)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise InvalidRequest("Request body must be valid JSON")
    if not isinstance(body, dict):
        raise InvalidRequest("Request body must be an object")
    return body


def _quantity_from_body(body: dict) -> int:
    value = body.get("quantity")
    if not isinstance(value, int):
        raise InvalidRequest("quantity must be an integer")
    return value


def _require_uuid(value: object, *, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidRequest(f"{field} must be a valid UUID")
    valid = validate_uuid(value, field=field)
    if valid is None:
        raise InvalidRequest(f"{field} must be a valid UUID")
    return valid
