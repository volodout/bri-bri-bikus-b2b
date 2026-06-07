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

CATALOG_CATEGORIES = "/api/v1/catalog/categories"
CATALOG_TREE = "/api/v1/catalog/categories/tree"
CATALOG_BREADCRUMBS = "/api/v1/catalog/breadcrumbs"


def _cat(id: str, name: str, parent_id: str | None, level: int, path: str) -> dict:
    return {
        "id": id,
        "name": name,
        "parent_id": parent_id,
        "level": level,
        "path": path,
        "is_active": True,
        "created_at": "2026-01-01T00:00:00Z",
    }


def _catalog_fixture() -> list[dict]:
    return [
        _cat(ELECTRONICS, "Электроника", None, 0, "electronics"),
        _cat(SMARTPHONES, "Смартфоны", ELECTRONICS, 1, "electronics/smartphones"),
        _cat(ANDROID, "Android", SMARTPHONES, 2, "electronics/smartphones/android"),
        _cat(IPHONE, "iPhone", SMARTPHONES, 2, "electronics/smartphones/iphone"),
        _cat(CLOTHES, "Одежда", None, 0, "clothes"),
    ]


def _assert_error_contract(body: dict, *, expected_code: str) -> None:
    assert "detail" not in body, f"framework default leaked: {body!r}"
    assert set(body.keys()) == {"code", "message"}, f"unexpected keys: {body!r}"
    assert body["code"] == expected_code
    assert isinstance(body["message"], str) and body["message"]


# ===========================================================================
# Flat list (GET /api/v1/catalog/categories) -> CategoryRef[]
# ===========================================================================
async def test_flat_categories_return_category_ref_array(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/categories"
        return httpx.Response(200, json=_catalog_fixture())

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_CATEGORIES)

    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 5

    smartphones = next(c for c in body if c["id"] == SMARTPHONES)
    assert smartphones["name"] == "Смартфоны"
    assert smartphones["parent_id"] == ELECTRONICS
    assert smartphones["level"] == 1
    assert smartphones["path"] == ["electronics", "smartphones"]
    assert "is_active" not in smartphones
    assert "created_at" not in smartphones
    assert "children" not in smartphones


# ===========================================================================
# Tree (GET /api/v1/catalog/categories/tree) -> CategoryTreeNode[]
# ===========================================================================
async def test_category_tree_returns_nested_array(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/categories"
        return httpx.Response(200, json=_catalog_fixture())

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_TREE)

    assert response.status_code == 200
    roots = response.json()
    assert isinstance(roots, list)
    assert len(roots) == 2

    roots_by_id = {root["id"]: root for root in roots}
    assert roots_by_id.keys() == {ELECTRONICS, CLOTHES}

    electronics = roots_by_id[ELECTRONICS]
    assert electronics["parent_id"] is None
    assert electronics["level"] == 0
    assert electronics["path"] == ["electronics"]
    assert len(electronics["children"]) == 1

    smartphones = electronics["children"][0]
    assert smartphones["id"] == SMARTPHONES
    assert smartphones["level"] == 1
    assert smartphones["path"] == ["electronics", "smartphones"]
    assert len(smartphones["children"]) == 2
    assert {c["id"] for c in smartphones["children"]} == {ANDROID, IPHONE}
    for leaf in smartphones["children"]:
        assert leaf["children"] == []
        assert leaf["level"] == 2

    assert roots_by_id[CLOTHES]["children"] == []


# ---------------------------------------------------------------------------
# Edge case: a node points at a missing parent -> 422 ORPHAN_NODE.
# ---------------------------------------------------------------------------
async def test_orphan_node_returns_422(client, b2b_recorder):
    flat = [
        _cat(ELECTRONICS, "Электроника", None, 0, "electronics"),
        _cat(SMARTPHONES, "Смартфоны", GHOST_PARENT, 1, "ghost/smartphones"),
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=flat)

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_TREE)

    assert response.status_code == 422
    _assert_error_contract(response.json(), expected_code="ORPHAN_NODE")


# ---------------------------------------------------------------------------
# Edge case: B2B returns nothing -> 200 with an empty array on both endpoints.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", [CATALOG_CATEGORIES, CATALOG_TREE])
async def test_empty_categories_return_200_empty_array(client, b2b_recorder, path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(path)

    assert response.status_code == 200
    assert response.json() == []


# ===========================================================================
# Category details (GET /api/v1/catalog/categories/{id}) — B2C extension,
# proxied to B2B.
# ===========================================================================
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
            f"{CATALOG_CATEGORIES}/{SMARTPHONES}",
            params={"include_product_count": "true"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == SMARTPHONES
    assert body["product_count"] == 1542


async def test_unknown_category_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "NOT_FOUND", "message": "Category not found"})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(f"{CATALOG_CATEGORIES}/{SMARTPHONES}")

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="NOT_FOUND")


async def test_category_details_invalid_uuid_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(f"{CATALOG_CATEGORIES}/not-a-uuid")

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


# ===========================================================================
# Breadcrumbs (GET /api/v1/catalog/breadcrumbs) — B2C extension, proxied.
# ===========================================================================
async def test_breadcrumbs_return_path_from_root(client, b2b_recorder):
    b2b_payload = {
        "data": [
            {"id": ELECTRONICS, "name": "Электроника", "level": 0, "is_current": False},
            {"id": SMARTPHONES, "name": "Смартфоны", "level": 1, "is_current": True},
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
        response = await ac.get(CATALOG_BREADCRUMBS, params={"category_id": SMARTPHONES})

    assert response.status_code == 200
    body = response.json()
    assert body["data"][0]["level"] == 0
    assert body["data"][-1]["is_current"] is True


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
        response = await ac.get(CATALOG_BREADCRUMBS, params={"product_id": PRODUCT_ID})

    assert response.status_code == 200
    assert response.json()["meta"]["resolved_via"] == "product_id"


async def test_ambiguous_params_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called when both params are present")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(
            CATALOG_BREADCRUMBS,
            params={"category_id": SMARTPHONES, "product_id": PRODUCT_ID},
        )

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")
    assert "only one" in response.json()["message"].lower()


async def test_breadcrumbs_missing_params_returns_400(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called without any param")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_BREADCRUMBS)

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


@pytest.mark.parametrize(
    ("param_name", "value"),
    [
        ("category_id", "not-a-uuid"),
        ("product_id", "12345"),
    ],
)
async def test_breadcrumbs_invalid_uuid_returns_400(client, b2b_recorder, param_name, value):
    def handler(request: httpx.Request) -> httpx.Response:  # pragma: no cover
        raise AssertionError("B2B must not be called for invalid uuid")

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_BREADCRUMBS, params={param_name: value})

    assert response.status_code == 400
    _assert_error_contract(response.json(), expected_code="INVALID_REQUEST")


async def test_breadcrumbs_unknown_category_returns_404(client, b2b_recorder):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"code": "NOT_FOUND", "message": "Category not found"})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(CATALOG_BREADCRUMBS, params={"category_id": SMARTPHONES})

    assert response.status_code == 404
    _assert_error_contract(response.json(), expected_code="NOT_FOUND")
