from __future__ import annotations

import httpx
import pytest

ELECTRONICS = "123e4567-e89b-12d3-a456-426614174002"
SMARTPHONES = "123e4567-e89b-12d3-a456-426614174003"
ANDROID = "123e4567-e89b-12d3-a456-426614174004"
IPHONE = "123e4567-e89b-12d3-a456-426614174005"
CLOTHES = "123e4567-e89b-12d3-a456-426614174010"
PRODUCT_ID = "770e8400-e29b-41d4-a716-446655440002"
GHOST_PARENT = "999e4567-e89b-12d3-a456-426614174999"


def _flat(id: str, name: str, parent_id: str | None) -> dict:
    return {"id": id, "name": name, "parent_id": parent_id}


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert set(body.keys()) == {"code", "message"}, f"unexpected keys: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


# ===========================================================================
# Category tree (GET /api/v1/categories)
# ===========================================================================


# ---------------------------------------------------------------------------
# Happy path: B2C assembles a nested tree from B2B's flat list.
# ---------------------------------------------------------------------------
async def test_category_tree_returns_nested_structure(client, b2b_recorder):
    flat = [
        _flat(ELECTRONICS, "Электроника", None),
        _flat(SMARTPHONES, "Смартфоны", ELECTRONICS),
        _flat(ANDROID, "Android", SMARTPHONES),
        _flat(IPHONE, "iPhone", SMARTPHONES),
        _flat(CLOTHES, "Одежда", None),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/categories"
        return httpx.Response(200, json={"items": flat})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/categories")

    assert response.status_code == 200
    body = response.json()
    items = body["items"]

    assert len(items) == 2
    roots_by_id = {root["id"]: root for root in items}
    assert roots_by_id.keys() == {ELECTRONICS, CLOTHES}

    electronics = roots_by_id[ELECTRONICS]
    assert electronics["parent_id"] is None
    assert len(electronics["children"]) == 1

    smartphones = electronics["children"][0]
    assert smartphones["id"] == SMARTPHONES
    assert smartphones["parent_id"] == ELECTRONICS
    assert len(smartphones["children"]) == 2
    child_ids = {c["id"] for c in smartphones["children"]}
    assert child_ids == {ANDROID, IPHONE}
    for leaf in smartphones["children"]:
        assert leaf["children"] == []

    clothes = roots_by_id[CLOTHES]
    assert clothes["children"] == []


# ---------------------------------------------------------------------------
# Edge case: a node points at a parent id that does not exist in the flat list.
# B2C refuses to render a half-broken tree -> 422 with ORPHAN_NODE.
# ---------------------------------------------------------------------------
async def test_orphan_node_returns_422(client, b2b_recorder):
    flat = [
        _flat(ELECTRONICS, "Электроника", None),
        _flat(SMARTPHONES, "Смартфоны", GHOST_PARENT),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": flat})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/categories")

    assert response.status_code == 422
    _assert_error_contract(response.json(), expected_code="ORPHAN_NODE")


# ---------------------------------------------------------------------------
# Edge case: B2B returns nothing -> 200 with empty items.
# ---------------------------------------------------------------------------
async def test_empty_category_tree_returns_200_empty_list(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": []})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/categories")

    assert response.status_code == 200
    assert response.json() == {"items": []}


# ===========================================================================
# Category details (GET /api/v1/categories/{id})
# ===========================================================================


# ---------------------------------------------------------------------------
# Happy path: B2C proxies through to B2B; include_product_count forwards.
# ---------------------------------------------------------------------------
async def test_category_details_proxy_with_product_count(client, b2b_recorder):
    b2b_payload = {
        "id": SMARTPHONES,
        "name": "Смартфоны",
        "slug": "smartphones",
        "description": "Мобильные телефоны",
        "parent": {"id": ELECTRONICS, "name": "Электроника", "slug": "electronics"},
        "product_count": 1542,
        "is_active": True,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/api/v1/categories/{SMARTPHONES}"
        assert request.url.params.get("include_product_count") == "true"
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            f"/api/v1/categories/{SMARTPHONES}",
            params={"include_product_count": "true"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == SMARTPHONES
    assert body["product_count"] == 1542


# ---------------------------------------------------------------------------
# Edge case: nonexistent category id -> upstream 404 -> public 404.
# ---------------------------------------------------------------------------
async def test_unknown_category_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"code": "NOT_FOUND", "message": "Category not found"},
        )

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(f"/api/v1/categories/{SMARTPHONES}")

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="NOT_FOUND")


# ---------------------------------------------------------------------------
# Edge case: bad UUID in path -> 400, B2B never called.
# ---------------------------------------------------------------------------
async def test_category_details_invalid_uuid_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/categories/not-a-uuid")

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


# ===========================================================================
# Breadcrumbs (GET /api/v1/breadcrumbs)
# ===========================================================================


# ---------------------------------------------------------------------------
# Happy path: chain from root to leaf, current node marked is_current=true.
# ---------------------------------------------------------------------------
async def test_breadcrumbs_return_path_from_root(client, b2b_recorder):
    b2b_payload = {
        "data": [
            {
                "id": ELECTRONICS,
                "slug": "electronics",
                "name": "Электроника",
                "url": "/catalog/electronics",
                "level": 0,
                "is_current": False,
            },
            {
                "id": SMARTPHONES,
                "slug": "smartphones",
                "name": "Смартфоны",
                "url": "/catalog/electronics/smartphones",
                "level": 1,
                "is_current": True,
            },
        ],
        "meta": {"resolved_via": "category_id", "category_id": SMARTPHONES},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/breadcrumbs"
        assert request.url.params.get("category_id") == SMARTPHONES
        assert request.url.params.get("product_id") is None
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/breadcrumbs", params={"category_id": SMARTPHONES})

    assert response.status_code == 200
    body = response.json()
    assert body == b2b_payload
    assert body["data"][0]["level"] == 0
    assert body["data"][-1]["is_current"] is True


# ---------------------------------------------------------------------------
# Happy path: product_id resolves to a category chain.
# ---------------------------------------------------------------------------
async def test_breadcrumbs_resolves_product_id(client, b2b_recorder):
    b2b_payload = {
        "data": [
            {"id": ELECTRONICS, "name": "Электроника", "level": 0, "is_current": False},
            {"id": SMARTPHONES, "name": "Смартфоны", "level": 1, "is_current": True},
        ],
        "meta": {"resolved_via": "product_id", "product_id": PRODUCT_ID},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params.get("product_id") == PRODUCT_ID
        assert request.url.params.get("category_id") is None
        return httpx.Response(200, json=b2b_payload)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/breadcrumbs", params={"product_id": PRODUCT_ID})

    assert response.status_code == 200
    assert response.json()["meta"]["resolved_via"] == "product_id"


# ---------------------------------------------------------------------------
# Edge case: both params at once -> 400 INVALID_REQUEST, B2B never called.
# ---------------------------------------------------------------------------
async def test_ambiguous_params_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called when both params are present")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            "/api/v1/breadcrumbs",
            params={"category_id": SMARTPHONES, "product_id": PRODUCT_ID},
        )

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert "only one" in response.json()["message"].lower()


# ---------------------------------------------------------------------------
# Edge case: no params -> 400 INVALID_REQUEST, B2B never called.
# ---------------------------------------------------------------------------
async def test_breadcrumbs_missing_params_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called without any param")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/breadcrumbs")

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


# ---------------------------------------------------------------------------
# Edge case: invalid UUIDs are rejected locally, B2B never called.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("param_name", "value"),
    [
        ("category_id", "not-a-uuid"),
        ("product_id", "12345"),
    ],
)
async def test_breadcrumbs_invalid_uuid_returns_400(
    client, b2b_recorder, param_name, value,
):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/breadcrumbs", params={param_name: value})

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


# ---------------------------------------------------------------------------
# Edge case: upstream 404 (category exists but resolution failed) -> 404.
# ---------------------------------------------------------------------------
async def test_breadcrumbs_unknown_category_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={"code": "NOT_FOUND", "message": "Category not found"},
        )

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get("/api/v1/breadcrumbs", params={"category_id": SMARTPHONES})

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="NOT_FOUND")
