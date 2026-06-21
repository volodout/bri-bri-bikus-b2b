from __future__ import annotations

from typing import Any, Protocol

from app.cart import CartRepository


class EventIdempotencyRepository(Protocol):
    async def mark_processed(self, key: str, event_type: str) -> bool: ...
    async def aclose(self) -> None: ...


class InMemoryEventIdempotencyRepository:
    def __init__(self) -> None:
        self._keys: set[str] = set()

    async def mark_processed(self, key: str, event_type: str) -> bool:
        if key in self._keys:
            return False
        self._keys.add(key)
        return True

    async def aclose(self) -> None:
        return None


class PostgresEventIdempotencyRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def mark_processed(self, key: str, event_type: str) -> bool:
        from uuid import UUID
        pool = await self._get_pool()
        async with pool.acquire() as conn:
            result = await conn.execute(
                """
                INSERT INTO processed_events (idempotency_key, event_type)
                VALUES ($1, $2)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                UUID(key),
                event_type,
            )
        return result == "INSERT 1"

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg
            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


_PRODUCT_BLOCKED_EVENTS = {"PRODUCT_BLOCKED", "PRODUCT_HARD_BLOCKED"}
_PRODUCT_DELETED_EVENTS = {"PRODUCT_DELETED"}
_SKU_STOCK_EVENTS = {"SKU_OUT_OF_STOCK"}


class ProductEventService:
    def __init__(
        self,
        event_repo: EventIdempotencyRepository,
        cart_repo: CartRepository,
    ) -> None:
        self._events = event_repo
        self._cart = cart_repo

    async def handle(self, event_type: str, idempotency_key: str, payload: dict) -> bool:
        is_new = await self._events.mark_processed(idempotency_key, event_type)
        if not is_new:
            return False

        if event_type in _PRODUCT_BLOCKED_EVENTS:
            product_id = payload.get("product_id")
            if product_id:
                await self._cart.mark_by_product_id(str(product_id), "PRODUCT_BLOCKED")

        elif event_type in _PRODUCT_DELETED_EVENTS:
            product_id = payload.get("product_id")
            if product_id:
                await self._cart.mark_by_product_id(str(product_id), "PRODUCT_DELETED")

        elif event_type in _SKU_STOCK_EVENTS:
            sku_id = payload.get("sku_id")
            if sku_id:
                await self._cart.mark_by_sku_id(str(sku_id), "OUT_OF_STOCK")

        return True
