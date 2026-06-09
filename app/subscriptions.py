from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.b2b_client import B2BClient
from app.errors import (
    B2BUnavailable,
    Conflict,
    FavoritesB2BUnavailable,
    InvalidNotifyOn,
    NotFound,
    ProductNotFound,
)

ALLOWED_EVENTS = ("BACK_IN_STOCK", "PRICE_DROP")


@dataclass(frozen=True)
class ProductSubscription:
    id: str
    user_id: str
    product_id: str
    notify_on: tuple[str, ...]
    created_at: datetime


class ProductSubscriptionRepository(Protocol):
    async def add(
        self,
        user_id: str,
        product_id: str,
        notify_on: tuple[Any, ...],
    ) -> ProductSubscription: ...

    async def remove(self, user_id: str, product_id: str) -> None: ...

    async def aclose(self) -> None: ...


class InMemoryProductSubscriptionRepository:
    def __init__(self) -> None:
        self._items: dict[tuple[str, str], ProductSubscription] = {}

    async def add(
        self,
        user_id: str,
        product_id: str,
        notify_on: tuple[str, ...],
    ) -> ProductSubscription:
        key = (user_id, product_id)
        if key in self._items:
            raise Conflict("SUBSCRIPTION_ALREADY_EXISTS", "Subscription already exists")

        subscription = ProductSubscription(
            id=str(uuid4()),
            user_id=user_id,
            product_id=product_id,
            notify_on=notify_on,
            created_at=datetime.now(timezone.utc),
        )
        self._items[key] = subscription
        return subscription

    async def remove(self, user_id: str, product_id: str) -> None:
        self._items.pop((user_id, product_id), None)

    async def aclose(self) -> None:
        return None


class PostgresProductSubscriptionRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def add(
        self,
        user_id: str,
        product_id: str,
        notify_on: tuple[str, ...],
    ) -> ProductSubscription:
        subscription_id = uuid4()
        user_uuid = UUID(user_id)
        product_uuid = UUID(product_id)
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                row = await connection.fetchrow(
                    """
                    INSERT INTO product_subscriptions (id, user_id, product_id, notify_on)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (user_id, product_id) DO NOTHING
                    RETURNING id::text, user_id::text, product_id::text, notify_on, created_at
                    """,
                    subscription_id,
                    user_uuid,
                    product_uuid,
                    list(notify_on),
                )

        if row is None:
            raise Conflict("SUBSCRIPTION_ALREADY_EXISTS", "Subscription already exists")
        return _subscription_from_row(row)

    async def remove(self, user_id: str, product_id: str) -> None:
        user_uuid = UUID(user_id)
        product_uuid = UUID(product_id)
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM product_subscriptions
                WHERE user_id = $1 AND product_id = $2
                """,
                user_uuid,
                product_uuid,
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


class ProductSubscriptionService:
    def __init__(
        self,
        repository: ProductSubscriptionRepository,
        b2b_client: B2BClient,
    ) -> None:
        self._repository = repository
        self._b2b_client = b2b_client

    async def subscribe(
        self,
        user_id: str,
        product_id: str,
        events: tuple[Any, ...],
    ) -> None:
        events = validate_events(events)
        try:
            await self._b2b_client.get_product(product_id)
        except NotFound as exc:
            raise ProductNotFound(exc.message)
        except B2BUnavailable as exc:
            raise FavoritesB2BUnavailable(exc.message)

        await self._repository.add(user_id, product_id, events)

    async def unsubscribe(self, user_id: str, product_id: str) -> None:
        await self._repository.remove(user_id, product_id)


def validate_events(values: tuple[Any, ...]) -> tuple[str, ...]:
    if not values:
        raise InvalidNotifyOn("events must contain at least one value")

    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or value not in ALLOWED_EVENTS:
            raise InvalidNotifyOn("events contains unsupported value")
        if value not in result:
            result.append(value)
    return tuple(result)


def _subscription_from_row(row: Any) -> ProductSubscription:
    return ProductSubscription(
        id=str(row["id"]),
        user_id=str(row["user_id"]),
        product_id=str(row["product_id"]),
        notify_on=tuple(row["notify_on"]),
        created_at=_parse_datetime(row["created_at"]),
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
