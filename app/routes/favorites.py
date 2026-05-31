from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from app.auth import user_id_from_jwt
from app.errors import InvalidRequest
from app.favorites import FavoriteService
from app.query_parsing import validate_pagination, validate_uuid

router = APIRouter()


def get_favorite_service(request: Request) -> FavoriteService:
    return request.app.state.favorite_service


@router.post("/api/v1/favorites/{product_id}")
async def add_to_favorites(request: Request, product_id: str) -> JSONResponse:
    product_id = _require_uuid(product_id, field="product_id")
    user_id = user_id_from_jwt(request)

    service = get_favorite_service(request)
    body, status_code = await service.add(user_id, product_id)
    return JSONResponse(status_code=status_code, content=body)


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
