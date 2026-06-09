from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID

from app.b2b_client import B2BClient
from app.errors import B2BUnavailable, FavoritesB2BUnavailable
from app.serializers import to_public_product


@dataclass(frozen=True)
class Favorite:
    user_id: str
    product_id: str
    added_at: datetime


class FavoriteRepository(Protocol):
    async def add(self, user_id: str, product_id: str) -> tuple[Favorite, bool]: ...

    async def remove(self, user_id: str, product_id: str) -> None: ...

    async def list_for_user(self, user_id: str) -> list[Favorite]: ...

    async def count_for_user_product(self, user_id: str, product_id: str) -> int: ...

    async def aclose(self) -> None: ...


class InMemoryFavoriteRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Favorite] = {}

    async def add(self, user_id: str, product_id: str) -> tuple[Favorite, bool]:
        key = (user_id, product_id)
        existing = self._items.get(key)
        if existing is not None:
            return existing, False

        favorite = Favorite(
            user_id=user_id,
            product_id=product_id,
            added_at=datetime.now(timezone.utc),
        )
        self._items[key] = favorite
        return favorite, True

    async def remove(self, user_id: str, product_id: str) -> None:
        self._items.pop((user_id, product_id), None)

    async def list_for_user(self, user_id: str) -> list[Favorite]:
        favorites = [
            item
            for (item_user_id, _), item in self._items.items()
            if item_user_id == user_id
        ]
        return sorted(favorites, key=lambda item: item.added_at, reverse=True)

    async def count_for_user_product(self, user_id: str, product_id: str) -> int:
        return int((user_id, product_id) in self._items)

    async def aclose(self) -> None:
        return None


class PostgresFavoriteRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def add(self, user_id: str, product_id: str) -> tuple[Favorite, bool]:
        user_uuid = UUID(user_id)
        product_uuid = UUID(product_id)
        added_at = datetime.now(timezone.utc)
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    WITH inserted AS (
                        INSERT INTO favorites (user_id, product_id, added_at)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (user_id, product_id) DO NOTHING
                        RETURNING user_id::text AS user_id, product_id::text AS product_id, added_at, true AS created
                    )
                    SELECT user_id, product_id, added_at, created
                    FROM inserted
                    UNION ALL
                    SELECT user_id::text AS user_id, product_id::text AS product_id, added_at, false AS created
                    FROM favorites
                    WHERE user_id = $1 AND product_id = $2
                    AND NOT EXISTS (SELECT 1 FROM inserted)
                    LIMIT 1
                    """,
                    user_uuid,
                    product_uuid,
                    added_at,
                )

        return _favorite_from_row(row), bool(row["created"])

    async def remove(self, user_id: str, product_id: str) -> None:
        user_uuid = UUID(user_id)
        product_uuid = UUID(product_id)
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                "DELETE FROM favorites WHERE user_id = $1 AND product_id = $2",
                user_uuid,
                product_uuid,
            )

    async def list_for_user(self, user_id: str) -> list[Favorite]:
        user_uuid = UUID(user_id)
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT user_id::text, product_id::text, added_at
                FROM favorites
                WHERE user_id = $1
                ORDER BY added_at DESC
                """,
                user_uuid,
            )
        return [_favorite_from_row(row) for row in rows]

    async def count_for_user_product(self, user_id: str, product_id: str) -> int:
        user_uuid = UUID(user_id)
        product_uuid = UUID(product_id)
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            return int(
                await connection.fetchval(
                    """
                    SELECT COUNT(*)
                    FROM favorites
                    WHERE user_id = $1 AND product_id = $2
                    """,
                    user_uuid,
                    product_uuid,
                )
            )

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


class FavoriteService:
    def __init__(self, repository: FavoriteRepository, b2b_client: B2BClient) -> None:
        self._repository = repository
        self._b2b_client = b2b_client

    async def add(self, user_id: str, product_id: str) -> None:
        try:
            await self._b2b_client.get_product(product_id)
        except B2BUnavailable as exc:
            raise FavoritesB2BUnavailable(exc.message)
        await self._repository.add(user_id, product_id)

    async def remove(self, user_id: str, product_id: str) -> None:
        await self._repository.remove(user_id, product_id)

    async def list(self, user_id: str, *, limit: int, offset: int) -> dict:
        favorites = await self._repository.list_for_user(user_id)
        if not favorites:
            return {"items": [], "total_count": 0, "limit": limit, "offset": offset}

        try:
            payload = await self._b2b_client.list_products_by_ids([item.product_id for item in favorites])
        except B2BUnavailable as exc:
            raise FavoritesB2BUnavailable(exc.message)
        products = payload.get("items") or []
        products_by_id = {
            str(product["id"]): product
            for product in products
            if isinstance(product, dict) and product.get("id") is not None
        }

        items = []
        for favorite in favorites:
            product = products_by_id.get(favorite.product_id)
            if product is None:
                continue
            items.append(to_public_product(product))

        return {
            "items": items[offset : offset + limit],
            "total_count": len(items),
            "limit": limit,
            "offset": offset,
        }


def serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _favorite_from_row(row: Any) -> Favorite:
    return Favorite(
        user_id=str(row["user_id"]),
        product_id=str(row["product_id"]),
        added_at=_parse_datetime(row["added_at"]),
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized).astimezone(timezone.utc)
