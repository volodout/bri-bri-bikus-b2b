from __future__ import annotations

import httpx
import pytest


def _list_payload(items: list[dict]) -> dict:
    return {"items": items, "total_count": len(items), "limit": 20, "offset": 0}


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert set(body.keys()) == {"code", "message"}, f"unexpected keys: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


# ---------------------------------------------------------------------------
# Happy path: GET /api/v1/products?search=... proxies the term to B2B and
# returns the items B2B matched on title/description.
# ---------------------------------------------------------------------------
async def test_search_returns_matching_products(client, b2b_recorder):
    b2b_payload = _list_payload(
        [
            {
                "id": "770e8400-e29b-41d4-a716-446655440002",
                "title": "Беспроводные наушники Sony WH-1000XM5",
                "image": "https://cdn.neomarket.ru/images/sony-wh1000xm5.jpg",
                "price": 3499000,
                "in_stock": True,
                "is_in_cart": False,
            },
            {
                "id": "770e8400-e29b-41d4-a716-446655440003",
                "title": "AirPods Pro 2",
                "image": "https://cdn.neomarket.ru/images/airpods-pro2.jpg",
                "price": 2199000,
                "in_stock": True,
                "is_in_cart": False,
            },
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/products"
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params=[("search", "наушники"), ("limit", "20"), ("offset", "0")],
        )

    assert response.status_code == 200
    body = response.json()
    assert body == b2b_payload
    assert body["total_count"] == 2
    assert len(body["items"]) == 2

    query = b2b_recorder.last_query
    assert ("search", "наушники") in query
    assert ("limit", "20") in query
    assert ("offset", "0") in query


# ---------------------------------------------------------------------------
# Edge case: search query shorter than 3 chars -> 400, B2B never called.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("short_value", ["a", "ab", " ", "  "])
async def test_short_query_returns_400(client, b2b_recorder, short_value):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for short query")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/products", params={"search": short_value})

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert "3" in response.json()["message"]
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: SQL/LIKE special characters (%, _, ') must NOT break B2C: they
# are forwarded verbatim, and B2B is responsible for SQL-escaping before LIKE.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "search_value",
    [
        "iPhone%15",
        "model_99",
        "кофе'арабика",
        "100%_'pure",
        "drop'; DROP TABLE products;--",
    ],
)
async def test_special_chars_do_not_break_query(client, b2b_recorder, search_value):
    received: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        received["search"] = request.url.params.get("search") or ""
        return httpx.Response(200, json=_list_payload([]))

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/products", params={"search": search_value})

    assert response.status_code == 200
    assert response.json() == _list_payload([])
    assert received["search"] == search_value


# ---------------------------------------------------------------------------
# Edge case: no matches -> 200 with empty list (NOT 404).
# ---------------------------------------------------------------------------
async def test_empty_results_returns_200(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert (request.url.params.get("search") or "") == "несуществующий товар"
        return httpx.Response(200, json=_list_payload([]))

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params={"search": "несуществующий товар"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_count"] == 0


# ---------------------------------------------------------------------------
# Bonus: search composes with filters/sort/category_id from US-CAT-01.
# ---------------------------------------------------------------------------
async def test_search_combines_with_filters_and_category(client, b2b_recorder):
    category_id = "123e4567-e89b-12d3-a456-426614174001"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_list_payload([]))

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products",
            params=[
                ("search", "наушники"),
                ("category_id", category_id),
                ("filters[brand]", "Sony"),
                ("sort", "price_asc"),
            ],
        )

    assert response.status_code == 200
    query = b2b_recorder.last_query
    assert ("search", "наушники") in query
    assert ("category_id", category_id) in query
    assert ("filters[brand]", "Sony") in query
    assert ("sort", "price_asc") in query


# ---------------------------------------------------------------------------
# Bonus: queries over 255 chars -> 400 (OpenAPI maxLength).
# ---------------------------------------------------------------------------
async def test_long_query_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for over-long query")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/products", params={"search": "x" * 256})

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert "255" in response.json()["message"]
    assert b2b_recorder.requests == []
