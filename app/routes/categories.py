from __future__ import annotations

from fastapi import APIRouter, Request

from app.b2b_client import B2BClient
from app.categories import assemble_category_tree
from app.errors import InvalidRequest
from app.query_parsing import validate_uuid

router = APIRouter()


def get_b2b_client(request: Request) -> B2BClient:
    return request.app.state.b2b_client


@router.get("/api/v1/categories")
async def list_categories(request: Request) -> dict:
    client = get_b2b_client(request)
    payload = await client.list_categories()
    items = payload.get("items") or []
    tree = assemble_category_tree(items)
    return {"items": tree}


@router.get("/api/v1/categories/{category_id}")
async def get_category(request: Request, category_id: str) -> dict:
    validate_uuid(category_id, field="id")
    include_product_count = request.query_params.get("include_product_count") == "true"
    client = get_b2b_client(request)
    return await client.get_category(
        category_id,
        include_product_count=include_product_count,
    )


@router.get("/api/v1/breadcrumbs")
async def get_breadcrumbs(request: Request) -> dict:
    category_id = request.query_params.get("category_id")
    product_id = request.query_params.get("product_id")

    if category_id and product_id:
        raise InvalidRequest("only one of category_id or product_id must be provided")
    if not category_id and not product_id:
        raise InvalidRequest("category_id or product_id must be provided")

    upstream_query: list[tuple[str, str]] = []
    if category_id:
        validate_uuid(category_id, field="category_id")
        upstream_query.append(("category_id", category_id))
    elif product_id:
        validate_uuid(product_id, field="product_id")
        upstream_query.append(("product_id", product_id))

    client = get_b2b_client(request)
    return await client.get_breadcrumbs(upstream_query)
