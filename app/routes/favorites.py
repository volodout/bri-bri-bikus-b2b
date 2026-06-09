from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.auth import user_id_from_jwt
from app.errors import InvalidRequest
from app.favorites import FavoriteService
from app.query_parsing import validate_pagination, validate_uuid
from app.subscriptions import ProductSubscriptionService

router = APIRouter()


def get_favorite_service(request: Request) -> FavoriteService:
    return request.app.state.favorite_service


def get_subscription_service(request: Request) -> ProductSubscriptionService:
    return request.app.state.subscription_service


@router.put("/api/v1/favorites/{product_id}", status_code=204)
async def add_to_favorites(request: Request, product_id: str) -> Response:
    product_id = _require_uuid(product_id, field="product_id")
    user_id = user_id_from_jwt(request)

    service = get_favorite_service(request)
    await service.add(user_id, product_id)
    return Response(status_code=204)


@router.delete("/api/v1/favorites/{product_id}", status_code=204)
async def remove_from_favorites(request: Request, product_id: str) -> Response:
    product_id = _require_uuid(product_id, field="product_id")
    user_id = user_id_from_jwt(request)

    service = get_favorite_service(request)
    await service.remove(user_id, product_id)
    return Response(status_code=204)


@router.get("/api/v1/favorites")
async def list_favorites(request: Request) -> dict:
    user_id = user_id_from_jwt(request)
    limit, offset = _pagination_from_query(request)

    service = get_favorite_service(request)
    return await service.list(user_id, limit=limit, offset=offset)


@router.post("/api/v1/favorites/{product_id}/subscribe", status_code=204)
async def subscribe_to_product(request: Request, product_id: str) -> Response:
    product_id = _require_uuid(product_id, field="product_id")
    user_id = user_id_from_jwt(request)
    body = await _json_body(request)
    events = _events_from_body(body)

    service = get_subscription_service(request)
    await service.subscribe(user_id, product_id, events)
    return Response(status_code=204)


@router.delete("/api/v1/favorites/{product_id}/subscribe", status_code=204)
async def unsubscribe_from_product(request: Request, product_id: str) -> Response:
    product_id = _require_uuid(product_id, field="product_id")
    user_id = user_id_from_jwt(request)

    service = get_subscription_service(request)
    await service.unsubscribe(user_id, product_id)
    return Response(status_code=204)


def _pagination_from_query(request: Request) -> tuple[int, int]:
    limit_raw = request.query_params.get("limit")
    offset_raw = request.query_params.get("offset")
    try:
        limit = int(limit_raw) if limit_raw is not None else None
        offset = int(offset_raw) if offset_raw is not None else None
    except ValueError:
        raise InvalidRequest("limit and offset must be integers")
    return validate_pagination(limit, offset)


def _require_uuid(value: str, *, field: str) -> str:
    valid = validate_uuid(value, field=field)
    if valid is None:
        raise InvalidRequest(f"{field} must be a valid UUID")
    return valid


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise InvalidRequest("Request body must be valid JSON")
    if not isinstance(body, dict):
        raise InvalidRequest("Request body must be an object")
    return body


def _events_from_body(body: dict) -> tuple[object, ...]:
    events = body.get("events")
    if not isinstance(events, list):
        return ()
    return tuple(events)
