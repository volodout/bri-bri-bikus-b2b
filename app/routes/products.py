from __future__ import annotations

from fastapi import APIRouter, Request

from app.b2b_client import B2BClient
from app.errors import InvalidRequest
from app.query_parsing import (
    extract_filters,
    validate_pagination,
    validate_search,
    validate_similar_pagination,
    validate_sort,
    validate_uuid,
)
from app.serializers import to_public_product

router = APIRouter()


def get_b2b_client(request: Request) -> B2BClient:
    return request.app.state.b2b_client


@router.get("/api/v1/catalog/products")
async def list_products(request: Request) -> dict:
    raw_params = list(request.query_params.multi_items())

    limit_raw = request.query_params.get("limit")
    offset_raw = request.query_params.get("offset")
    try:
        limit = int(limit_raw) if limit_raw is not None else None
        offset = int(offset_raw) if offset_raw is not None else None
    except ValueError:
        raise InvalidRequest("limit and offset must be integers")

    limit, offset = validate_pagination(limit, offset)
    sort = validate_sort(request.query_params.get("sort"))
    category_id = validate_uuid(request.query_params.get("category_id"), field="category_id")
    search = validate_search(request.query_params.get("q"))

    upstream_query: list[tuple[str, str]] = [
        ("limit", str(limit)),
        ("offset", str(offset)),
    ]
    if sort is not None:
        upstream_query.append(("sort", sort))
    if category_id is not None:
        upstream_query.append(("category_id", category_id))
    if search is not None:
        upstream_query.append(("search", search))
    upstream_query.extend(extract_filters(raw_params))

    client = get_b2b_client(request)
    return await client.list_products(upstream_query)


@router.get("/api/v1/catalog/products/{product_id}")
async def get_product(request: Request, product_id: str) -> dict:
    validate_uuid(product_id, field="id")
    client = get_b2b_client(request)
    payload = await client.get_product(product_id)
    return to_public_product(payload)


@router.get("/api/v1/products/{product_id}/similar")
async def get_similar(request: Request, product_id: str) -> dict:
    validate_uuid(product_id, field="id")

    category_raw = request.query_params.get("category")
    if not category_raw:
        raise InvalidRequest("category is required")
    category = validate_uuid(category_raw, field="category")

    limit_raw = request.query_params.get("limit")
    offset_raw = request.query_params.get("offset")
    try:
        limit = int(limit_raw) if limit_raw is not None else None
        offset = int(offset_raw) if offset_raw is not None else None
    except ValueError:
        raise InvalidRequest("limit and offset must be integers")

    limit, offset = validate_similar_pagination(limit, offset)

    upstream_query: list[tuple[str, str]] = [
        ("category", category),
        ("limit", str(limit)),
        ("offset", str(offset)),
    ]

    client = get_b2b_client(request)
    return await client.get_similar_products(product_id, upstream_query)
