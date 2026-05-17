from __future__ import annotations

from fastapi import APIRouter, Request

from app.b2b_client import B2BClient
from app.errors import InvalidRequest
from app.query_parsing import extract_filters, validate_uuid

router = APIRouter()


def get_b2b_client(request: Request) -> B2BClient:
    return request.app.state.b2b_client


@router.get("/api/v1/catalog/facets")
async def get_facets(request: Request) -> dict:
    raw_params = list(request.query_params.multi_items())

    category_id_raw = request.query_params.get("category_id")
    if not category_id_raw:
        raise InvalidRequest("category_id is required")
    category_id = validate_uuid(category_id_raw, field="category_id")

    upstream_query: list[tuple[str, str]] = [("category_id", category_id)]
    upstream_query.extend(extract_filters(raw_params))

    client = get_b2b_client(request)
    return await client.get_facets(upstream_query)
