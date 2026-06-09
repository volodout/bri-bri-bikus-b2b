from __future__ import annotations

import uuid

import httpx

PRODUCT_ID = "770e8400-e29b-41d4-a716-446655440002"
CATEGORY_ID = "123e4567-e89b-12d3-a456-426614174001"

CATALOG_SIMILAR = f"/api/v1/catalog/products/{PRODUCT_ID}/similar"
B2B_SIMILAR = f"/api/v1/products/{PRODUCT_ID}/similar"


def _short_item(product_id: str, title: str, *, min_price: int = 9999000) -> dict:
    return {
        "id": product_id,
        "title": title,
        "slug": product_id,
        "status": "MODERATED",
        "category_id": CATEGORY_ID,
        "created_at": "2026-01-01T00:00:00Z",
        "min_price": min_price,
        "cover_image": f"https://cdn.neomarket.ru/{product_id}.jpg",
    }


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert set(body.keys()) == {"code", "message"}, f"unexpected keys: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


# ---------------------------------------------------------------------------
# Happy path: response is a flat CatalogProductCard[] (openapi.yaml), default
# limit is 10, no `category` is sent (B2B derives it), current product excluded.
# ---------------------------------------------------------------------------
async def test_similar_returns_card_array_with_spec_fields(client, b2b_recorder):
    items = [
        _short_item(f"770e8400-e29b-41d4-a716-44665544{n:04d}", f"Similar #{n}")
        for n in range(100, 108)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == B2B_SIMILAR
        assert request.headers.get("X-Service-Key") == "test-service-key"
        params = request.url.params
        assert params.get("limit") == "10"
        assert params.get("category") is None
        return httpx.Response(200, json=items)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 8
    assert all(item["id"] != PRODUCT_ID for item in body), (
        "current product must be excluded from similar results"
    )

    first = body[0]
    assert first["name"] == "Similar #100"
    assert first["min_price"] == 9999000
    assert first["has_stock"] is True
    expected_url = f"https://cdn.neomarket.ru/{first['id']}.jpg"
    assert first["images"] == [
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, expected_url)),
            "url": expected_url,
            "ordering": 0,
        }
    ]
    assert "title" not in first
    assert "price" not in first
    assert "in_stock" not in first


# ---------------------------------------------------------------------------
# Contract: every image is an ImageRef with required id (uuid), url, ordering.
# The id is derived deterministically from the url, so repeated requests for
# the same product return a stable id.
# ---------------------------------------------------------------------------
async def test_image_carries_stable_uuid_id(client, b2b_recorder):
    item = _short_item("770e8400-e29b-41d4-a716-446655440050", "Imaged")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[item])

    b2b_recorder.set_handler(handler)

    async with client as ac:
        first = (await ac.get(CATALOG_SIMILAR)).json()[0]
        again = (await ac.get(CATALOG_SIMILAR)).json()[0]

    image = first["images"][0]
    assert set(image.keys()) == {"id", "url", "ordering"}
    assert uuid.UUID(image["id"])
    assert image["id"] == again["images"][0]["id"]


# ---------------------------------------------------------------------------
# Defensive: has_stock reflects the upstream value when B2B supplies it,
# instead of being hardcoded to True.
# ---------------------------------------------------------------------------
async def test_has_stock_reflects_b2b_value(client, b2b_recorder):
    item = _short_item("770e8400-e29b-41d4-a716-446655440060", "Sold out")
    item["has_stock"] = False

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[item])

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR)

    assert response.status_code == 200
    assert response.json()[0]["has_stock"] is False


# ---------------------------------------------------------------------------
# Limit: custom value within 1..50 is forwarded to B2B verbatim.
# ---------------------------------------------------------------------------
async def test_custom_limit_forwarded(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("limit") == "25"
        return httpx.Response(200, json=[_short_item(PRODUCT_ID.replace("2", "9"), "X")])

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR, params={"limit": "25"})

    assert response.status_code == 200
    assert ("limit", "25") in b2b_recorder.last_query


# ---------------------------------------------------------------------------
# Edge case: no similar products -> 200 with empty array (frontend hides the
# block).
# ---------------------------------------------------------------------------
async def test_empty_returns_200_empty_array(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == B2B_SIMILAR
        return httpx.Response(200, json=[])

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR)

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Bonus: parent-category fallback is owned by B2B. B2C sends only the limit
# and proxies whatever mixed list B2B returns, mapped to cards.
# ---------------------------------------------------------------------------
async def test_parent_category_fallback_is_proxied(client, b2b_recorder):
    items = [
        _short_item("770e8400-e29b-41d4-a716-446655440010", "Same cat #1"),
        _short_item("770e8400-e29b-41d4-a716-446655440020", "Parent cat #1"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("category") is None
        return httpx.Response(200, json=items)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR, params={"limit": "4"})

    assert response.status_code == 200
    body = response.json()
    assert [item["name"] for item in body] == ["Same cat #1", "Parent cat #1"]


# ---------------------------------------------------------------------------
# SECURITY: unknown / private upstream fields are dropped by allow-list
# construction — only the public card fields reach the buyer.
# ---------------------------------------------------------------------------
async def test_unknown_upstream_fields_are_dropped(client, b2b_recorder):
    item = _short_item("770e8400-e29b-41d4-a716-446655440030", "Leaky")
    item["cost_price"] = 7500000
    item["seller_margin"] = 0.3
    item["warehouse_location"] = "MSK-A1"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[item])

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR)

    card = response.json()[0]
    assert "cost_price" not in card
    assert "seller_margin" not in card
    assert "warehouse_location" not in card
    assert set(card.keys()) <= {"id", "name", "min_price", "has_stock", "images", "slug"}


# ---------------------------------------------------------------------------
# Edge case: unknown product id -> B2B 404 -> B2C 404.
# ---------------------------------------------------------------------------
async def test_unknown_product_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "NOT_FOUND", "message": "Product not found"})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR)

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="NOT_FOUND")


# ---------------------------------------------------------------------------
# Edge case: upstream 400 is surfaced with the {code, message} contract.
# ---------------------------------------------------------------------------
async def test_upstream_400_is_surfaced(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"code": "INVALID_REQUEST", "message": "Bad request"})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR)

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


# ---------------------------------------------------------------------------
# Edge case: limit above the spec max (50) -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_limit_above_50_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called when limit is out of range")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR, params={"limit": "51"})

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert "50" in response.json()["message"]
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: invalid product UUID in path -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_invalid_product_uuid_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/catalog/products/not-a-uuid/similar")

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Edge case: B2B unavailable -> 502 with the {code, message} contract.
# ---------------------------------------------------------------------------
async def test_b2b_unavailable_returns_502(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unreachable", request=request)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_SIMILAR)

    assert response.status_code == 502
    _assert_error_contract(response.json(), expected_code="UPSTREAM_UNAVAILABLE")
