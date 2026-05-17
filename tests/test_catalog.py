from __future__ import annotations

import httpx
import pytest

CATEGORY_ID = "123e4567-e89b-12d3-a456-426614174001"


# ---------------------------------------------------------------------------
# Happy path: GET /api/v1/products with filters, sort, pagination
# ---------------------------------------------------------------------------
async def test_catalog_returns_filtered_sorted_products(client, b2b_recorder):
    b2b_payload = {
        "items": [
            {
                "id": "770e8400-e29b-41d4-a716-446655440002",
                "title": "iPhone 15 Pro Max",
                "image": "https://cdn.neomarket.ru/images/iphone15.jpg",
                "price": 12999000,
                "in_stock": True,
                "is_in_cart": False,
            },
            {
                "id": "770e8400-e29b-41d4-a716-446655440003",
                "title": "iPhone 15",
                "image": "https://cdn.neomarket.ru/images/iphone15-std.jpg",
                "price": 9999000,
                "in_stock": True,
                "is_in_cart": False,
            },
        ],
        "total_count": 2,
        "limit": 20,
        "offset": 0,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/products"
        assert request.headers.get("X-Service-Key") == "test-service-key"
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params=[
                ("category_id", CATEGORY_ID),
                ("filters[brand]", "Apple"),
                ("filters[color]", "черный"),
                ("sort", "price_asc"),
                ("limit", "20"),
                ("offset", "0"),
            ],
        )

    assert response.status_code == 200
    body = response.json()
    assert body == b2b_payload
    assert body["items"][0]["price"] == 12999000  # копейки, integer

    query = b2b_recorder.last_query
    assert ("category_id", CATEGORY_ID) in query
    assert ("filters[brand]", "Apple") in query
    assert ("filters[color]", "черный") in query
    assert ("sort", "price_asc") in query
    assert ("limit", "20") in query
    assert ("offset", "0") in query


# ---------------------------------------------------------------------------
# Happy path: GET /api/v1/catalog/facets returns per-value counts
# ---------------------------------------------------------------------------
async def test_facets_return_counts_per_filter_value(client, b2b_recorder):
    b2b_payload = {
        "category_id": CATEGORY_ID,
        "facets": [
            {
                "name": "brand",
                "values": [
                    {"value": "Apple", "count": 124},
                    {"value": "Samsung", "count": 98},
                    {"value": "Xiaomi", "count": 76},
                ],
            },
            {
                "name": "color",
                "values": [
                    {"value": "черный", "count": 60},
                    {"value": "белый", "count": 40},
                ],
            },
        ],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/catalog/facets"
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/catalog/facets",
            params=[
                ("category_id", CATEGORY_ID),
                ("filters[brand]", "Apple"),
            ],
        )

    assert response.status_code == 200
    body = response.json()
    assert body["category_id"] == CATEGORY_ID
    brand_facet = next(f for f in body["facets"] if f["name"] == "brand")
    assert {"value": "Apple", "count": 124} in brand_facet["values"]
    assert sum(v["count"] for v in brand_facet["values"]) == 124 + 98 + 76

    query = b2b_recorder.last_query
    assert ("category_id", CATEGORY_ID) in query
    assert ("filters[brand]", "Apple") in query


# ---------------------------------------------------------------------------
# Edge case: invalid sort -> 400 with list of allowed values
# ---------------------------------------------------------------------------
async def test_invalid_sort_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called when sort is invalid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/products", params={"sort": "totally_invalid"})

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    msg = body["message"]
    for allowed in ("rating", "popularity", "price_asc", "price_desc", "date_desc", "discount_desc"):
        assert allowed in msg, f"expected '{allowed}' in error message, got: {msg!r}"
    assert b2b_recorder.requests == []  # never called upstream


# ---------------------------------------------------------------------------
# Edge case: B2B unavailable -> 502/503
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "failure_mode",
    ["connect_error", "timeout", "upstream_503"],
)
async def test_b2b_unavailable_returns_502(client, b2b_recorder, failure_mode):
    def handler(request: httpx.Request) -> httpx.Response:
        if failure_mode == "connect_error":
            raise httpx.ConnectError("upstream unreachable", request=request)
        if failure_mode == "timeout":
            raise httpx.ReadTimeout("upstream slow", request=request)
        return httpx.Response(503, json={"message": "B2B maintenance"})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params={"category_id": CATEGORY_ID},
        )

    assert response.status_code in (502, 503)
    body = response.json()
    assert body["code"] == "UPSTREAM_UNAVAILABLE"
    assert isinstance(body["message"], str) and body["message"]


# ---------------------------------------------------------------------------
# Bonus coverage — pulled from canon edge cases (not in DoD names but cheap)
# ---------------------------------------------------------------------------
async def test_facets_missing_category_id_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called without category_id")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/catalog/facets")

    assert response.status_code == 400
    body = response.json()
    assert body["code"] == "INVALID_REQUEST"
    assert "category_id" in body["message"]


async def test_empty_category_returns_empty_list(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [], "total_count": 0, "limit": 20, "offset": 0})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params={"category_id": CATEGORY_ID},
        )

    assert response.status_code == 200
    assert response.json() == {"items": [], "total_count": 0, "limit": 20, "offset": 0}
