from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Protocol

from app.b2b_client import B2BClient
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


class FavoriteService:
    def __init__(self, repository: FavoriteRepository, b2b_client: B2BClient) -> None:
        self._repository = repository
        self._b2b_client = b2b_client

    async def add(self, user_id: str, product_id: str) -> tuple[dict, int]:
        await self._b2b_client.get_product(product_id)
        favorite, created = await self._repository.add(user_id, product_id)
        return serialize_favorite_mutation(favorite, created=created), 201 if created else 200

    async def remove(self, user_id: str, product_id: str) -> None:
        await self._repository.remove(user_id, product_id)

    async def list(self, user_id: str, *, limit: int, offset: int) -> dict:
        favorites = await self._repository.list_for_user(user_id)
        if not favorites:
            return {"items": [], "total": 0}

        payload = await self._b2b_client.list_products_by_ids([item.product_id for item in favorites])
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
            items.append(
                {
                    "product": to_public_product(product),
                    "added_at": serialize_datetime(favorite.added_at),
                }
            )

        return {"items": items[offset : offset + limit], "total": len(items)}


def serialize_favorite_mutation(favorite: Favorite, *, created: bool) -> dict:
    return {
        "product_id": favorite.product_id,
        "user_id": favorite.user_id,
        "added_at": serialize_datetime(favorite.added_at),
        "message": "Product added to favorites" if created else "Product already in favorites",
    }


def serialize_datetime(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
