from __future__ import annotations

import httpx

PRODUCT_ID = "770e8400-e29b-41d4-a716-446655440002"
CATEGORY_ID = "123e4567-e89b-12d3-a456-426614174001"
PARENT_CATEGORY_ID = "123e4567-e89b-12d3-a456-426614174000"


def _short_item(product_id: str, title: str, price: int = 9999000) -> dict:
    return {
        "id": product_id,
        "title": title,
        "image": f"https://cdn.neomarket.ru/{product_id}.jpg",
        "price": price,
        "in_stock": True,
        "is_in_cart": False,
    }


def _list_payload(items: list[dict], *, limit: int = 8, offset: int = 0) -> dict:
    return {"items": items, "total_count": len(items), "limit": limit, "offset": offset}


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert set(body.keys()) == {"code", "message"}, f"unexpected keys: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


# ---------------------------------------------------------------------------
# Happy path: up to 8 similar items from the same category; current product
# is excluded by B2B and must not appear in the response.
# ---------------------------------------------------------------------------
async def test_similar_returns_up_to_8_from_same_category(client, b2b_recorder):
    similar_items = [
        _short_item(f"770e8400-e29b-41d4-a716-44665544{n:04d}", f"Similar item #{n}")
        for n in range(100, 108)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/products/{PRODUCT_ID}/similar"
        assert request.headers.get("X-Service-Key") == "test-service-key"
        params = request.url.params
        assert params.get("category") == CATEGORY_ID
        assert params.get("limit") == "8"
        return httpx.Response(200, json=_list_payload(similar_items))

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{PRODUCT_ID}/similar",
            params={"category": CATEGORY_ID},
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 8
    assert body["total_count"] == 8
    assert body["limit"] == 8
    assert all(item["id"] != PRODUCT_ID for item in body["items"]), (
        "current product must be excluded from similar results"
    )


# ---------------------------------------------------------------------------
# Edge case: no similar products in the category -> 200 with items: [].
# The frontend hides the "Similar products" block.
# ---------------------------------------------------------------------------
async def test_empty_category_returns_200_empty_list(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/products/{PRODUCT_ID}/similar"
        return httpx.Response(200, json=_list_payload([]))

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{PRODUCT_ID}/similar",
            params={"category": CATEGORY_ID},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total_count"] == 0


# ---------------------------------------------------------------------------
# Edge case: unknown product id -> B2B returns 404 -> B2C returns 404.
# ---------------------------------------------------------------------------
async def test_unknown_product_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"code": "NOT_FOUND", "message": "Product not found"},
        )

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{PRODUCT_ID}/similar",
            params={"category": CATEGORY_ID},
        )

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="NOT_FOUND")


# ---------------------------------------------------------------------------
# Bonus: nonexistent (syntactically valid) category id -> B2B returns 400
# with `Nonexistent category id` -> B2C surfaces 400 with the same code.
# ---------------------------------------------------------------------------
async def test_nonexistent_category_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"code": "INVALID_REQUEST", "message": "Nonexistent category id"},
        )

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{PRODUCT_ID}/similar",
            params={"category": CATEGORY_ID},
        )

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


# ---------------------------------------------------------------------------
# Bonus: parent-category fallback is owned by B2B. B2C simply proxies; when
# B2B widens the pool to the parent category and returns a mixed list, B2C
# passes it through unchanged.
# ---------------------------------------------------------------------------
async def test_parent_category_fallback_is_proxied(client, b2b_recorder):
    items = [
        _short_item("770e8400-e29b-41d4-a716-446655440010", "Same cat #1"),
        _short_item("770e8400-e29b-41d4-a716-446655440011", "Same cat #2"),
        _short_item("770e8400-e29b-41d4-a716-446655440020", "Parent cat #1"),
        _short_item("770e8400-e29b-41d4-a716-446655440021", "Parent cat #2"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_list_payload(items))

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{PRODUCT_ID}/similar",
            params={"category": CATEGORY_ID, "limit": "4"},
        )

    assert response.status_code == 200
    body = response.json()
    assert len(body["items"]) == 4

    query = b2b_recorder.last_query
    assert ("category", CATEGORY_ID) in query
    assert ("limit", "4") in query


# ---------------------------------------------------------------------------
# Edge case: limit boundary — max is 20 for /similar (canon B2C-4), unlike
# the catalog's max of 100.
# ---------------------------------------------------------------------------
async def test_limit_above_20_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called when limit is out of range")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{PRODUCT_ID}/similar",
            params={"category": CATEGORY_ID, "limit": "21"},
        )

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert "20" in response.json()["message"]


# ---------------------------------------------------------------------------
# Edge case: missing required `category` query param -> 400.
# ---------------------------------------------------------------------------
async def test_missing_category_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called without category")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(f"/api/v1/products/{PRODUCT_ID}/similar")

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert "category" in response.json()["message"]


# ---------------------------------------------------------------------------
# Edge case: invalid product UUID in path -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_invalid_product_uuid_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/products/not-a-uuid/similar",
            params={"category": CATEGORY_ID},
        )

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


# ---------------------------------------------------------------------------
# Edge case: invalid category UUID -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_invalid_category_uuid_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid category uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/products/{PRODUCT_ID}/similar",
            params={"category": "not-a-uuid"},
        )

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
