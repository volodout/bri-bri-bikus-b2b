from __future__ import annotations

import httpx

PRODUCT_ID = "770e8400-e29b-41d4-a716-446655440002"
SKU_ID_A = "660e8400-e29b-41d4-a716-446655440001"
SKU_ID_B = "660e8400-e29b-41d4-a716-446655440002"

CATALOG_CARD = f"/api/v1/catalog/products/{PRODUCT_ID}"
B2B_CARD = f"/api/v1/products/{PRODUCT_ID}"


def _full_b2b_payload(skus: list[dict]) -> dict:
    return {
        "id": PRODUCT_ID,
        "seller_id": "550e8400-e29b-41d4-a716-446655440000",
        "category_id": "123e4567-e89b-12d3-a456-426614174001",
        "slug": "iphone-15-pro-max",
        "title": "iPhone 15 Pro Max",
        "description": "Флагманский смартфон Apple 2024 года с чипом A17 Pro",
        "status": "MODERATED",
        "images": [
            {"id": "111e8400-e29b-41d4-a716-000000000001",
             "url": "https://cdn.neomarket.ru/iphone15-front.jpg", "ordering": 0},
            {"id": "111e8400-e29b-41d4-a716-000000000002",
             "url": "https://cdn.neomarket.ru/iphone15-back.jpg", "ordering": 1},
        ],
        "characteristics": [
            {"id": "222e8400-e29b-41d4-a716-000000000001", "name": "Бренд", "value": "Apple"},
            {"id": "222e8400-e29b-41d4-a716-000000000002",
             "name": "Страна-производитель", "value": "Китай"},
        ],
        "skus": skus,
    }


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert set(body.keys()) == {"code", "message"}, f"unexpected keys: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


# ---------------------------------------------------------------------------
# Happy path: response matches openapi.yaml CatalogProductDetail —
# name/min_price/has_stock/images required, SKUs use available_quantity and
# discount is folded into price/old_price.
# ---------------------------------------------------------------------------
async def test_product_card_returns_full_data_with_skus(client, b2b_recorder):
    b2b_payload = _full_b2b_payload(
        [
            {
                "id": SKU_ID_A,
                "name": "256GB Black",
                "price": 12999000,
                "discount": 0,
                "stock_quantity": 10,
                "active_quantity": 10,
                "article": "IPH15PM-256-BLK",
                "images": [
                    {"id": "333e8400-e29b-41d4-a716-000000000001",
                     "url": "https://cdn.neomarket.ru/iphone15-black-256.jpg", "ordering": 0},
                ],
                "characteristics": [
                    {"id": "444e1", "name": "Цвет", "value": "Чёрный"},
                    {"id": "444e2", "name": "Объём памяти", "value": "256 ГБ"},
                ],
            },
            {
                "id": SKU_ID_B,
                "name": "256GB White",
                "price": 12999000,
                "discount": 500000,
                "stock_quantity": 3,
                "active_quantity": 3,
                "article": "IPH15PM-256-WHT",
                "images": [],
                "characteristics": [
                    {"id": "444e3", "name": "Цвет", "value": "Белый"},
                ],
            },
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == B2B_CARD
        assert request.headers.get("X-Service-Key") == "test-service-key"
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CARD)

    assert response.status_code == 200
    body = response.json()

    assert body["id"] == PRODUCT_ID
    assert body["name"] == "iPhone 15 Pro Max"
    assert "title" not in body
    assert body["slug"] == "iphone-15-pro-max"
    assert body["description"].startswith("Флагманский")
    assert body["min_price"] == 12499000
    assert body["has_stock"] is True
    assert body["attributes"]["Бренд"] == "Apple"

    assert len(body["images"]) == 2
    assert body["images"][0]["url"].endswith("iphone15-front.jpg")
    assert body["images"][0]["ordering"] == 0
    assert "id" in body["images"][0]

    assert len(body["skus"]) == 2
    sku_a = next(s for s in body["skus"] if s["id"] == SKU_ID_A)
    assert sku_a["price"] == 12999000
    assert sku_a["old_price"] is None
    assert sku_a["available_quantity"] == 10
    assert sku_a["sku_code"] == "IPH15PM-256-BLK"
    assert sku_a["attributes"]["Цвет"] == "Чёрный"
    assert "discount" not in sku_a
    assert "active_quantity" not in sku_a

    sku_b = next(s for s in body["skus"] if s["id"] == SKU_ID_B)
    assert sku_b["price"] == 12499000
    assert sku_b["old_price"] == 12999000
    assert sku_b["available_quantity"] == 3


# ---------------------------------------------------------------------------
# SECURITY: cost_price and reserved_quantity must NEVER reach the buyer.
# Even if upstream B2B mistakenly includes them, the public serializer
# strips them by allow-list construction.
# ---------------------------------------------------------------------------
async def test_cost_price_absent_in_response(client, b2b_recorder):
    b2b_payload = _full_b2b_payload(
        [
            {
                "id": SKU_ID_A,
                "name": "128GB Black",
                "price": 9999000,
                "discount": 0,
                "stock_quantity": 5,
                "active_quantity": 5,
                "article": "IPH15PM-128-BLK",
                "images": [],
                "characteristics": [],
                "cost_price": 7500000,
                "reserved_quantity": 2,
            },
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CARD)

    assert response.status_code == 200
    body = response.json()
    assert "cost_price" not in body["skus"][0]
    assert "reserved_quantity" not in body["skus"][0]
    assert body["skus"][0]["price"] == 9999000
    assert body["skus"][0]["available_quantity"] == 5


# ---------------------------------------------------------------------------
# SECURITY (bonus): any unknown field on the upstream payload — product- or
# SKU-level — is dropped at the boundary. Adding a new private field on B2B
# does not leak unless the public allow-list is explicitly extended.
# ---------------------------------------------------------------------------
async def test_unknown_upstream_fields_are_dropped(client, b2b_recorder):
    b2b_payload = _full_b2b_payload(
        [
            {
                "id": SKU_ID_A,
                "name": "128GB",
                "price": 9999000,
                "discount": 0,
                "stock_quantity": 5,
                "active_quantity": 5,
                "article": "X",
                "images": [],
                "characteristics": [],
                "supplier_id": "supplier-internal-uuid",
                "warehouse_location": "MSK-A1-rack-13",
            },
        ],
    )
    b2b_payload["internal_audit_log"] = ["seller-touched-at-12:00"]
    b2b_payload["margin_target"] = 0.30

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CARD)

    body = response.json()
    assert "internal_audit_log" not in body
    assert "margin_target" not in body
    assert "status" not in body
    assert "seller_id" not in body
    assert "supplier_id" not in body["skus"][0]
    assert "warehouse_location" not in body["skus"][0]


# ---------------------------------------------------------------------------
# Edge case: blocked/deleted product -> B2B returns 404 -> B2C returns 404.
# ---------------------------------------------------------------------------
async def test_blocked_product_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == B2B_CARD
        return httpx.Response(
            404,
            json={"code": "NOT_FOUND", "message": "Product not found"},
        )

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CARD)

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="NOT_FOUND")


# ---------------------------------------------------------------------------
# Edge case: SKU with available_quantity=0 is still surfaced — the frontend
# disables "Add to cart" rather than hiding the variant; has_stock reflects
# the in-stock SKU and min_price covers only available variants.
# ---------------------------------------------------------------------------
async def test_sku_without_stock_is_shown_as_unavailable(client, b2b_recorder):
    b2b_payload = _full_b2b_payload(
        [
            {
                "id": SKU_ID_A,
                "name": "256GB Black (in stock)",
                "price": 12999000,
                "discount": 0,
                "stock_quantity": 7,
                "active_quantity": 7,
                "article": "A",
                "images": [],
                "characteristics": [],
            },
            {
                "id": SKU_ID_B,
                "name": "256GB White (out of stock)",
                "price": 11999000,
                "discount": 0,
                "stock_quantity": 0,
                "active_quantity": 0,
                "article": "B",
                "images": [],
                "characteristics": [],
            },
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CARD)

    assert response.status_code == 200
    body = response.json()
    assert body["has_stock"] is True
    assert body["min_price"] == 12999000

    out_of_stock = next(s for s in body["skus"] if s["id"] == SKU_ID_B)
    assert out_of_stock["available_quantity"] == 0

    in_stock = next(s for s in body["skus"] if s["id"] == SKU_ID_A)
    assert in_stock["available_quantity"] > 0


# ---------------------------------------------------------------------------
# Edge case: no SKU in stock -> has_stock=False, min_price still present
# (required field) as the cheapest variant overall.
# ---------------------------------------------------------------------------
async def test_all_skus_out_of_stock_keeps_min_price(client, b2b_recorder):
    b2b_payload = _full_b2b_payload(
        [
            {
                "id": SKU_ID_A,
                "name": "256GB",
                "price": 12999000,
                "discount": 0,
                "stock_quantity": 0,
                "active_quantity": 0,
                "article": "A",
                "images": [],
                "characteristics": [],
            },
            {
                "id": SKU_ID_B,
                "name": "128GB",
                "price": 9999000,
                "discount": 0,
                "stock_quantity": 0,
                "active_quantity": 0,
                "article": "B",
                "images": [],
                "characteristics": [],
            },
        ],
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CARD)

    assert response.status_code == 200
    body = response.json()
    assert body["has_stock"] is False
    assert body["min_price"] == 9999000


# ---------------------------------------------------------------------------
# Bonus: invalid UUID in path -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_invalid_uuid_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/catalog/products/not-a-uuid")

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert b2b_recorder.requests == []


# ---------------------------------------------------------------------------
# Bonus: B2B unavailable -> 502 with code/message contract.
# ---------------------------------------------------------------------------
async def test_product_card_b2b_unavailable_returns_502(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("upstream unreachable", request=request)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CARD)

    assert response.status_code == 502
    _assert_error_contract(response.json(), expected_code="UPSTREAM_UNAVAILABLE")
