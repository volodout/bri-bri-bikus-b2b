from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import httpx

from app.collections import CollectionProduct, ProductCollection


COLLECTION_ID = "550e8400-e29b-41d4-a716-446655440000"
SECOND_COLLECTION_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
PRODUCT_ID = "770e8400-e29b-41d4-a716-446655440000"
SECOND_PRODUCT_ID = "880e8400-e29b-41d4-a716-446655440000"
MISSING_PRODUCT_ID = "990e8400-e29b-41d4-a716-446655440000"


def collection(
    collection_id: str = COLLECTION_ID,
    *,
    title: str = "Hits",
    priority: int = 10,
    is_active: bool = True,
    start_date: date | None = None,
) -> ProductCollection:
    return ProductCollection(
        id=collection_id,
        title=title,
        description=f"{title} description",
        cover_image_url=f"/cdn/collections/{collection_id}.jpg",
        target_url=f"/collections/{collection_id}",
        priority=priority,
        is_active=is_active,
        start_date=start_date or date.today() - timedelta(days=1),
        created_at=datetime.now(timezone.utc) + timedelta(seconds=priority),
    )


def product_payload(product_id: str, *, title: str) -> dict:
    return {
        "id": product_id,
        "slug": title.lower().replace(" ", "-"),
        "title": title,
        "description": f"{title} description",
        "status": "MODERATED",
        "images": [{"url": f"/cdn/products/{product_id}.jpg"}],
        "cost_price": 100,
        "skus": [
            {
                "id": "7c9e6679-7425-40de-944b-e07fc1f90ae7",
                "name": "Default",
                "price": 1000,
                "active_quantity": 5,
                "reserved_quantity": 2,
            }
        ],
    }


async def test_collections_list_returns_metadata_without_products(client, collection_repository):
    collection_repository.add_collection(collection(COLLECTION_ID, title="New season", priority=20))
    collection_repository.add_collection(collection(SECOND_COLLECTION_ID, title="Hits", priority=10))
    collection_repository.add_collection(
        collection("7c9e6679-7425-40de-944b-e07fc1f90ae7", title="Inactive", priority=1, is_active=False)
    )
    collection_repository.add_collection(
        collection("8c9e6679-7425-40de-944b-e07fc1f90ae7", title="Future", priority=2, start_date=date.today() + timedelta(days=1))
    )
    collection_repository.add_product(CollectionProduct(COLLECTION_ID, PRODUCT_ID, 1))

    async with client as ac:
        response = await ac.get("/api/v1/main/collections")

    assert response.status_code == 200
    body = response.json()
    assert body["metadata"] == {"total_count": 2, "limit": 10, "offset": 0}
    assert [item["id"] for item in body["collections"]] == [SECOND_COLLECTION_ID, COLLECTION_ID]
    assert "items" not in body["collections"][0]
    assert "products" not in body["collections"][0]


async def test_collection_products_enriched_from_b2b(client, collection_repository, b2b_recorder):
    collection_repository.add_collection(collection())
    collection_repository.add_product(CollectionProduct(COLLECTION_ID, PRODUCT_ID, 1))
    collection_repository.add_product(CollectionProduct(COLLECTION_ID, SECOND_PRODUCT_ID, 2))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/products"
        assert request.url.params.get("ids") == f"{PRODUCT_ID},{SECOND_PRODUCT_ID}"
        return httpx.Response(
            200,
            json={
                "items": [
                    product_payload(PRODUCT_ID, title="Phone"),
                    product_payload(SECOND_PRODUCT_ID, title="Case"),
                ]
            },
        )

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(f"/api/v1/collections/{COLLECTION_ID}/products")

    assert response.status_code == 200
    body = response.json()
    assert body["collection_title"] == "Hits"
    assert body["total_products"] == 2
    assert [item["id"] for item in body["items"]] == [PRODUCT_ID, SECOND_PRODUCT_ID]
    assert body["unavailable_ids"] == []
    assert "cost_price" not in body["items"][0]
    assert "reserved_quantity" not in body["items"][0]["skus"][0]


async def test_unavailable_products_in_unavailable_ids(client, collection_repository, b2b_recorder):
    collection_repository.add_collection(collection())
    collection_repository.add_product(CollectionProduct(COLLECTION_ID, PRODUCT_ID, 1))
    collection_repository.add_product(CollectionProduct(COLLECTION_ID, MISSING_PRODUCT_ID, 2))

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/products"
        return httpx.Response(200, json={"items": [product_payload(PRODUCT_ID, title="Phone")]})

    b2b_recorder.set_handler(handler)

    async with client as ac:
        response = await ac.get(f"/api/v1/collections/{COLLECTION_ID}/products")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [PRODUCT_ID]
    assert body["unavailable_ids"] == [MISSING_PRODUCT_ID]


async def test_unknown_collection_returns_404(client):
    async with client as ac:
        response = await ac.get(f"/api/v1/collections/{COLLECTION_ID}/products")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


async def test_inactive_collection_products_returns_404(client, collection_repository):
    collection_repository.add_collection(collection(is_active=False))

    async with client as ac:
        response = await ac.get(f"/api/v1/collections/{COLLECTION_ID}/products")

    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
