from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from app.b2b_client import B2BClient
from app.errors import B2BUnavailable, InvalidRequest, NotFound, ServiceUnavailable
from app.serializers import to_public_product


@dataclass(frozen=True)
class ProductCollection:
    id: str
    title: str
    description: str | None
    cover_image_url: str | None
    target_url: str | None
    priority: int
    is_active: bool
    start_date: date | None
    created_at: datetime


@dataclass(frozen=True)
class CollectionProduct:
    collection_id: str
    product_id: str
    ordering: int


class CollectionRepository(Protocol):
    async def list_active(self, today: date, limit: int, offset: int) -> tuple[list[ProductCollection], int]: ...

    async def get(self, collection_id: str) -> ProductCollection | None: ...

    async def list_product_ids(self, collection_id: str, limit: int, offset: int) -> tuple[list[str], int]: ...

    async def aclose(self) -> None: ...


class InMemoryCollectionRepository:
    def __init__(
        self,
        collections: list[ProductCollection] | None = None,
        products: list[CollectionProduct] | None = None,
    ) -> None:
        self._collections: dict[str, ProductCollection] = {
            collection.id: collection for collection in collections or []
        }
        self._products: list[CollectionProduct] = products or []

    def add_collection(self, collection: ProductCollection) -> None:
        self._collections[collection.id] = collection

    def add_product(self, product: CollectionProduct) -> None:
        self._products.append(product)

    async def list_active(self, today: date, limit: int, offset: int) -> tuple[list[ProductCollection], int]:
        active = sorted(
            [collection for collection in self._collections.values() if _is_active_collection(collection, today)],
            key=lambda collection: (collection.priority, collection.created_at, collection.id),
        )
        return active[offset : offset + limit], len(active)

    async def get(self, collection_id: str) -> ProductCollection | None:
        return self._collections.get(collection_id)

    async def list_product_ids(self, collection_id: str, limit: int, offset: int) -> tuple[list[str], int]:
        products = sorted(
            [product for product in self._products if product.collection_id == collection_id],
            key=lambda product: (product.ordering, product.product_id),
        )
        return [product.product_id for product in products[offset : offset + limit]], len(products)

    async def aclose(self) -> None:
        return None


class PostgresCollectionRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def list_active(self, today: date, limit: int, offset: int) -> tuple[list[ProductCollection], int]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id::text, title, description, cover_image_url, target_url, priority,
                       is_active, start_date, created_at, count(*) OVER() AS total_count
                FROM collections
                WHERE is_active = true
                AND (start_date IS NULL OR start_date <= $1)
                ORDER BY priority ASC, created_at ASC, id ASC
                LIMIT $2 OFFSET $3
                """,
                today,
                limit,
                offset,
            )
        total_count = int(rows[0]["total_count"]) if rows else 0
        return [_collection_from_row(row) for row in rows], total_count

    async def get(self, collection_id: str) -> ProductCollection | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT id::text, title, description, cover_image_url, target_url, priority,
                       is_active, start_date, created_at
                FROM collections
                WHERE id = $1
                """,
                UUID(collection_id),
            )
        return _collection_from_row(row) if row is not None else None

    async def list_product_ids(self, collection_id: str, limit: int, offset: int) -> tuple[list[str], int]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT product_id::text, count(*) OVER() AS total_count
                FROM collection_products
                WHERE collection_id = $1
                ORDER BY ordering ASC, product_id ASC
                LIMIT $2 OFFSET $3
                """,
                UUID(collection_id),
                limit,
                offset,
            )
        total_count = int(rows[0]["total_count"]) if rows else 0
        return [str(row["product_id"]) for row in rows], total_count

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


class CollectionService:
    def __init__(self, repository: CollectionRepository, b2b_client: B2BClient) -> None:
        self._repository = repository
        self._b2b_client = b2b_client

    async def list_collections(self, limit: int, offset: int) -> dict:
        collections, total_count = await self._repository.list_active(datetime.now(timezone.utc).date(), limit, offset)
        return {
            "metadata": {"total_count": total_count, "limit": limit, "offset": offset},
            "collections": [_collection_payload(collection) for collection in collections],
        }

    async def get_collection_products(self, collection_id: str, limit: int, offset: int) -> dict:
        collection = await self._repository.get(collection_id)
        if collection is None or not _is_active_collection(collection, datetime.now(timezone.utc).date()):
            raise NotFound("Collection not found")

        product_ids, total_products = await self._repository.list_product_ids(collection_id, limit, offset)
        if not product_ids:
            return {
                "collection_title": collection.title,
                "total_products": total_products,
                "items": [],
                "unavailable_ids": [],
            }

        try:
            payload = await self._b2b_client.list_products_by_ids(product_ids)
        except B2BUnavailable as exc:
            raise ServiceUnavailable(exc.message)

        returned = {
            str(product["id"]): to_public_product(product)
            for product in payload.get("items") or []
            if isinstance(product, dict) and product.get("id") is not None
        }
        items = [returned[product_id] for product_id in product_ids if product_id in returned]
        unavailable_ids = [product_id for product_id in product_ids if product_id not in returned]
        return {
            "collection_title": collection.title,
            "total_products": total_products,
            "items": items,
            "unavailable_ids": unavailable_ids,
        }


def validate_collections_pagination(limit_raw: str | None, offset_raw: str | None) -> tuple[int, int]:
    try:
        limit = int(limit_raw) if limit_raw is not None else 10
        offset = int(offset_raw) if offset_raw is not None else 0
    except ValueError:
        raise InvalidRequest("limit and offset must be integers")
    if limit < 1 or limit > 50:
        raise InvalidRequest("limit must be between 1 and 50")
    if offset < 0:
        raise InvalidRequest("offset must be >= 0")
    return limit, offset


def validate_collection_products_pagination(limit_raw: str | None, offset_raw: str | None) -> tuple[int, int]:
    try:
        limit = int(limit_raw) if limit_raw is not None else 20
        offset = int(offset_raw) if offset_raw is not None else 0
    except ValueError:
        raise InvalidRequest("limit and offset must be integers")
    if limit < 1 or limit > 100:
        raise InvalidRequest("limit must be between 1 and 100")
    if offset < 0:
        raise InvalidRequest("offset must be >= 0")
    return limit, offset


def _is_active_collection(collection: ProductCollection, today: date) -> bool:
    if not collection.is_active:
        return False
    if collection.start_date is not None and collection.start_date > today:
        return False
    return True


def _collection_payload(collection: ProductCollection) -> dict:
    return {
        "id": collection.id,
        "title": collection.title,
        "description": collection.description,
        "cover_image_url": collection.cover_image_url,
        "target_url": collection.target_url,
        "priority": collection.priority,
        "start_date": collection.start_date.isoformat() if collection.start_date is not None else None,
    }


def _collection_from_row(row: Any) -> ProductCollection:
    return ProductCollection(
        id=str(row["id"]),
        title=row["title"],
        description=row["description"],
        cover_image_url=row["cover_image_url"],
        target_url=row["target_url"],
        priority=int(row["priority"]),
        is_active=bool(row["is_active"]),
        start_date=_parse_optional_date(row["start_date"]),
        created_at=_parse_datetime(row["created_at"]),
    )


def _parse_optional_date(value: date | datetime | str | None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
