from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.auth import optional_user_id_from_jwt
from app.b2b_client import B2BClient
from app.errors import (
    B2BUnavailable,
    CartItemNotFound,
    InsufficientStock,
    InvalidQuantity,
    MissingCartIdentity,
    NotFound,
    ServiceUnavailable,
    SkuNotAvailable,
    SkuNotFound,
)


@dataclass(frozen=True)
class CartIdentity:
    user_id: str | None
    session_id: str | None

    def without_session_for_user(self) -> CartIdentity:
        if self.user_id is None:
            return self
        return CartIdentity(user_id=self.user_id, session_id=None)


@dataclass(frozen=True)
class CartItem:
    id: str
    user_id: str | None
    session_id: str | None
    product_id: str
    sku_id: str
    quantity: int
    created_at: datetime
    updated_at: datetime


class CartRepository(Protocol):
    async def add(
        self,
        identity: CartIdentity,
        product_id: str,
        sku_id: str,
        quantity: int,
    ) -> tuple[CartItem, bool]: ...

    async def update_quantity(self, identity: CartIdentity, item_id: str, quantity: int) -> CartItem: ...

    async def remove(self, identity: CartIdentity, item_id: str) -> None: ...

    async def clear(self, identity: CartIdentity) -> None: ...

    async def list_items(self, identity: CartIdentity) -> list[CartItem]: ...

    async def merge_guest_into_user(self, user_id: str, session_id: str) -> None: ...

    async def aclose(self) -> None: ...


class InMemoryCartRepository:
    def __init__(self) -> None:
        self._items: dict[str, CartItem] = {}

    async def add(
        self,
        identity: CartIdentity,
        product_id: str,
        sku_id: str,
        quantity: int,
    ) -> tuple[CartItem, bool]:
        existing = self._find_by_sku(identity, sku_id)
        now = datetime.now(timezone.utc)
        if existing is not None:
            item = replace(existing, quantity=existing.quantity + quantity, updated_at=now)
            self._items[item.id] = item
            return item, False

        item = CartItem(
            id=str(uuid4()),
            user_id=identity.user_id,
            session_id=None if identity.user_id is not None else identity.session_id,
            product_id=product_id,
            sku_id=sku_id,
            quantity=quantity,
            created_at=now,
            updated_at=now,
        )
        self._items[item.id] = item
        return item, True

    async def update_quantity(self, identity: CartIdentity, item_id: str, quantity: int) -> CartItem:
        item = self._items.get(item_id)
        if item is None or not _belongs_to(item, identity):
            raise CartItemNotFound()
        updated = replace(item, quantity=quantity, updated_at=datetime.now(timezone.utc))
        self._items[item_id] = updated
        return updated

    async def remove(self, identity: CartIdentity, item_id: str) -> None:
        item = self._items.get(item_id)
        if item is None or not _belongs_to(item, identity):
            raise CartItemNotFound()
        del self._items[item_id]

    async def clear(self, identity: CartIdentity) -> None:
        for item_id in [item.id for item in self._items.values() if _belongs_to(item, identity)]:
            del self._items[item_id]

    async def list_items(self, identity: CartIdentity) -> list[CartItem]:
        return sorted(
            [item for item in self._items.values() if _belongs_to(item, identity)],
            key=lambda item: item.created_at,
        )

    async def merge_guest_into_user(self, user_id: str, session_id: str) -> None:
        guest_identity = CartIdentity(user_id=None, session_id=session_id)
        user_identity = CartIdentity(user_id=user_id, session_id=None)
        guest_items = await self.list_items(guest_identity)
        for guest_item in guest_items:
            auth_item = self._find_by_sku(user_identity, guest_item.sku_id)
            if auth_item is None:
                moved = replace(guest_item, user_id=user_id, session_id=None, updated_at=datetime.now(timezone.utc))
                self._items[moved.id] = moved
            else:
                merged = replace(
                    auth_item,
                    quantity=max(auth_item.quantity, guest_item.quantity),
                    updated_at=datetime.now(timezone.utc),
                )
                self._items[merged.id] = merged
                del self._items[guest_item.id]

    async def aclose(self) -> None:
        return None

    def _find_by_sku(self, identity: CartIdentity, sku_id: str) -> CartItem | None:
        for item in self._items.values():
            if item.sku_id == sku_id and _belongs_to(item, identity):
                return item
        return None


class PostgresCartRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def add(
        self,
        identity: CartIdentity,
        product_id: str,
        sku_id: str,
        quantity: int,
    ) -> tuple[CartItem, bool]:
        pool = await self._get_pool()
        item_id = uuid4()
        async with pool.acquire() as connection:
            async with connection.transaction():
                if identity.user_id is not None:
                    row = await connection.fetchrow(
                        """
                        WITH inserted AS (
                            INSERT INTO cart_items (id, user_id, session_id, product_id, sku_id, quantity)
                            VALUES ($1, $2, NULL, $3, $4, $5)
                            ON CONFLICT (user_id, sku_id) WHERE user_id IS NOT NULL
                            DO UPDATE SET quantity = cart_items.quantity + EXCLUDED.quantity, updated_at = now()
                            RETURNING id::text, user_id::text, session_id, product_id::text, sku_id::text,
                                      quantity, created_at, updated_at,
                                      (xmax = 0) AS created
                        )
                        SELECT *
                        FROM inserted
                        """,
                        item_id,
                        UUID(identity.user_id),
                        UUID(product_id),
                        UUID(sku_id),
                        quantity,
                    )
                else:
                    row = await connection.fetchrow(
                        """
                        WITH inserted AS (
                            INSERT INTO cart_items (id, user_id, session_id, product_id, sku_id, quantity)
                            VALUES ($1, NULL, $2, $3, $4, $5)
                            ON CONFLICT (session_id, sku_id) WHERE session_id IS NOT NULL
                            DO UPDATE SET quantity = cart_items.quantity + EXCLUDED.quantity, updated_at = now()
                            RETURNING id::text, user_id::text, session_id, product_id::text, sku_id::text,
                                      quantity, created_at, updated_at,
                                      (xmax = 0) AS created
                        )
                        SELECT *
                        FROM inserted
                        """,
                        item_id,
                        identity.session_id,
                        UUID(product_id),
                        UUID(sku_id),
                        quantity,
                    )
        return _cart_item_from_row(row), bool(row["created"])

    async def update_quantity(self, identity: CartIdentity, item_id: str, quantity: int) -> CartItem:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                UPDATE cart_items
                SET quantity = $2, updated_at = now()
                WHERE id = $1 AND (
                    ($3::uuid IS NOT NULL AND user_id = $3::uuid)
                    OR ($3::uuid IS NULL AND session_id = $4)
                )
                RETURNING id::text, user_id::text, session_id, product_id::text, sku_id::text,
                          quantity, created_at, updated_at
                """,
                UUID(item_id),
                quantity,
                UUID(identity.user_id) if identity.user_id else None,
                identity.session_id,
            )
        if row is None:
            raise CartItemNotFound()
        return _cart_item_from_row(row)

    async def remove(self, identity: CartIdentity, item_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            result = await connection.execute(
                """
                DELETE FROM cart_items
                WHERE id = $1 AND (
                    ($2::uuid IS NOT NULL AND user_id = $2::uuid)
                    OR ($2::uuid IS NULL AND session_id = $3)
                )
                """,
                UUID(item_id),
                UUID(identity.user_id) if identity.user_id else None,
                identity.session_id,
            )
        if result == "DELETE 0":
            raise CartItemNotFound()

    async def clear(self, identity: CartIdentity) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            await connection.execute(
                """
                DELETE FROM cart_items
                WHERE ($1::uuid IS NOT NULL AND user_id = $1::uuid)
                OR ($1::uuid IS NULL AND session_id = $2)
                """,
                UUID(identity.user_id) if identity.user_id else None,
                identity.session_id,
            )

    async def list_items(self, identity: CartIdentity) -> list[CartItem]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id::text, user_id::text, session_id, product_id::text, sku_id::text,
                       quantity, created_at, updated_at
                FROM cart_items
                WHERE ($1::uuid IS NOT NULL AND user_id = $1::uuid)
                OR ($1::uuid IS NULL AND session_id = $2)
                ORDER BY created_at ASC
                """,
                UUID(identity.user_id) if identity.user_id else None,
                identity.session_id,
            )
        return [_cart_item_from_row(row) for row in rows]

    async def merge_guest_into_user(self, user_id: str, session_id: str) -> None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            async with connection.transaction():
                guest_rows = await connection.fetch(
                    """
                    SELECT id, sku_id, quantity
                    FROM cart_items
                    WHERE session_id = $1
                    """,
                    session_id,
                )
                for row in guest_rows:
                    existing = await connection.fetchrow(
                        """
                        SELECT id, quantity
                        FROM cart_items
                        WHERE user_id = $1 AND sku_id = $2
                        """,
                        UUID(user_id),
                        row["sku_id"],
                    )
                    if existing is None:
                        await connection.execute(
                            """
                            UPDATE cart_items
                            SET user_id = $1, session_id = NULL, updated_at = now()
                            WHERE id = $2
                            """,
                            UUID(user_id),
                            row["id"],
                        )
                    else:
                        await connection.execute(
                            """
                            UPDATE cart_items
                            SET quantity = $1, updated_at = now()
                            WHERE id = $2
                            """,
                            max(existing["quantity"], row["quantity"]),
                            existing["id"],
                        )
                        await connection.execute("DELETE FROM cart_items WHERE id = $1", row["id"])
                await connection.execute("DELETE FROM cart_items WHERE session_id = $1", session_id)

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


class CartService:
    def __init__(self, repository: CartRepository, b2b_client: B2BClient) -> None:
        self._repository = repository
        self._b2b_client = b2b_client

    async def merge_if_needed(self, identity: CartIdentity) -> None:
        if identity.user_id is not None and identity.session_id:
            await self._repository.merge_guest_into_user(identity.user_id, identity.session_id)

    async def add_item(self, identity: CartIdentity, sku_id: str, quantity: int) -> dict:
        _validate_quantity(quantity)
        target_identity = identity.without_session_for_user()
        product, sku = await self._get_sku_payload(sku_id)
        existing = next(
            (item for item in await self._repository.list_items(target_identity) if item.sku_id == sku_id),
            None,
        )
        requested_quantity = quantity + (existing.quantity if existing is not None else 0)
        _ensure_sku_can_be_added(product, sku, requested_quantity)
        await self._repository.add(target_identity, str(product["id"]), sku_id, quantity)
        return await self._cart_response(target_identity)

    async def get_cart(self, identity: CartIdentity) -> dict:
        await self.merge_if_needed(identity)
        return await self._cart_response(identity.without_session_for_user())

    async def update_item(self, identity: CartIdentity, sku_id: str, quantity: int) -> dict:
        _validate_quantity(quantity)
        resolved_identity = identity.without_session_for_user()
        current = await self._find_item_by_sku(resolved_identity, sku_id)
        product, sku = await self._get_sku_payload(sku_id)
        _ensure_sku_can_be_added(product, sku, quantity)
        item = await self._repository.update_quantity(resolved_identity, current.id, quantity)
        response = await self._mutation_response(resolved_identity, item)
        response["message"] = "Cart item updated"
        return response

    async def get_item(self, identity: CartIdentity, item_id: str) -> dict:
        item = await self._find_item(identity.without_session_for_user(), item_id)
        cart = await self._cart_response(identity.without_session_for_user())
        enriched_item = next((value for value in cart["items"] if value["item_id"] == item.id), None)
        if enriched_item is None:
            raise CartItemNotFound()
        return enriched_item

    async def remove_item(self, identity: CartIdentity, item_id: str) -> dict:
        resolved_identity = identity.without_session_for_user()
        await self._repository.remove(resolved_identity, item_id)
        return await self._cart_response(resolved_identity)

    async def clear(self, identity: CartIdentity) -> None:
        await self._repository.clear(identity.without_session_for_user())

    async def _find_item(self, identity: CartIdentity, item_id: str) -> CartItem:
        for item in await self._repository.list_items(identity):
            if item.id == item_id:
                return item
        raise CartItemNotFound()

    async def _find_item_by_sku(self, identity: CartIdentity, sku_id: str) -> CartItem:
        for item in await self._repository.list_items(identity):
            if item.sku_id == sku_id:
                return item
        raise CartItemNotFound()

    async def _mutation_response(self, identity: CartIdentity, item: CartItem) -> dict:
        cart = await self._cart_response(identity)
        enriched_item = next((value for value in cart["items"] if value["item_id"] == item.id), None)
        if enriched_item is None:
            enriched_item = _unavailable_item(item, "PRODUCT_DELETED")
        return {"message": "Cart item updated", "item": enriched_item, "summary": cart["summary"]}

    async def _cart_response(self, identity: CartIdentity) -> dict:
        items = await self._repository.list_items(identity)
        if not items:
            return _empty_cart()

        products = await self._products_by_id(items)
        enriched = [_enrich_item(item, products.get(item.product_id)) for item in items]
        return _cart_payload(enriched)

    async def _products_by_id(self, items: list[CartItem]) -> dict[str, dict]:
        product_ids = []
        for item in items:
            if item.product_id not in product_ids:
                product_ids.append(item.product_id)
        try:
            payload = await self._b2b_client.list_products_by_ids(product_ids)
        except B2BUnavailable as exc:
            raise ServiceUnavailable(exc.message)
        products = payload.get("items") or []
        return {
            str(product["id"]): product
            for product in products
            if isinstance(product, dict) and product.get("id") is not None
        }

    async def _get_sku_payload(self, sku_id: str) -> tuple[dict, dict]:
        try:
            payload = await self._b2b_client.get_sku(sku_id)
        except NotFound as exc:
            raise SkuNotFound(exc.message)
        except B2BUnavailable as exc:
            raise ServiceUnavailable(exc.message)

        product = payload.get("product")
        sku = payload.get("sku")
        if not isinstance(product, dict) or not isinstance(sku, dict):
            raise SkuNotFound()
        return product, sku


def identity_from_request(request: Any) -> CartIdentity:
    user_id = optional_user_id_from_jwt(request)
    session_id = request.headers.get("X-Session-Id")
    if user_id is None and not session_id:
        raise MissingCartIdentity()
    return CartIdentity(user_id=user_id, session_id=session_id)


def _belongs_to(item: CartItem, identity: CartIdentity) -> bool:
    if identity.user_id is not None:
        return item.user_id == identity.user_id
    return item.session_id == identity.session_id


def _cart_item_from_row(row: Any) -> CartItem:
    return CartItem(
        id=str(row["id"]),
        user_id=str(row["user_id"]) if row["user_id"] is not None else None,
        session_id=row["session_id"],
        product_id=str(row["product_id"]),
        sku_id=str(row["sku_id"]),
        quantity=int(row["quantity"]),
        created_at=_parse_datetime(row["created_at"]),
        updated_at=_parse_datetime(row["updated_at"]),
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _validate_quantity(quantity: int) -> None:
    if quantity < 1:
        raise InvalidQuantity()


def _ensure_sku_can_be_added(product: dict, sku: dict, quantity: int) -> None:
    status = product.get("status")
    if status != "MODERATED":
        raise SkuNotAvailable()
    active_quantity = int(sku.get("active_quantity") or 0)
    if active_quantity <= 0:
        raise SkuNotAvailable()
    if active_quantity < quantity:
        raise InsufficientStock()


def _enrich_item(item: CartItem, product: dict | None) -> dict:
    if product is None:
        return _unavailable_item(item, "PRODUCT_DELETED")

    sku = _find_sku(product, item.sku_id)
    if sku is None:
        return _unavailable_item(item, _reason_from_product(product))

    stock = int(sku.get("active_quantity") or 0)
    reason = _unavailable_reason(product, sku)
    available = reason is None
    unit_price = int(sku.get("price") or 0)
    line_total = unit_price * item.quantity if available else 0
    return {
        "item_id": item.id,
        "sku_id": item.sku_id,
        "product_id": str(product.get("id") or item.product_id),
        "product_title": str(product.get("title") or ""),
        "sku_name": str(sku.get("name") or ""),
        "image_url": sku.get("image") or _first_product_image(product),
        "unit_price": unit_price,
        "quantity": item.quantity,
        "available_stock": stock,
        "line_total": line_total,
        "available": available,
        "unavailable_reason": reason,
    }


def _find_sku(product: dict, sku_id: str) -> dict | None:
    for sku in product.get("skus") or []:
        if isinstance(sku, dict) and str(sku.get("id")) == sku_id:
            return sku
    return None


def _unavailable_reason(product: dict, sku: dict) -> str | None:
    status = product.get("status")
    if status in ("BLOCKED",):
        return "PRODUCT_BLOCKED"
    if status in ("ON_MODERATION", "EDITED"):
        return "ON_MODERATION"
    if status in ("DELETED", "DELISTED"):
        return "PRODUCT_DELISTED"
    if int(sku.get("active_quantity") or 0) <= 0:
        return "OUT_OF_STOCK"
    return None


def _reason_from_product(product: dict) -> str:
    status = product.get("status")
    if status == "BLOCKED":
        return "PRODUCT_BLOCKED"
    if status in ("ON_MODERATION", "EDITED"):
        return "ON_MODERATION"
    if status in ("DELISTED",):
        return "PRODUCT_DELISTED"
    return "PRODUCT_DELETED"


def _unavailable_item(item: CartItem, reason: str) -> dict:
    return {
        "item_id": item.id,
        "sku_id": item.sku_id,
        "product_id": item.product_id,
        "product_title": "",
        "sku_name": "",
        "image_url": None,
        "unit_price": 0,
        "quantity": item.quantity,
        "available_stock": 0,
        "line_total": 0,
        "available": False,
        "unavailable_reason": reason,
    }


def _first_product_image(product: dict) -> str | None:
    images = product.get("images") or []
    if not images:
        return None
    first = images[0]
    if isinstance(first, dict):
        return first.get("url")
    return None


def _cart_payload(items: list[dict]) -> dict:
    available_items = [item for item in items if item["available"]]
    total_amount = sum(item["line_total"] for item in available_items)
    total_quantity = sum(item["quantity"] for item in items)
    checkout_items = [
        {
            "product_id": item["product_id"],
            "sku_id": item["sku_id"],
            "quantity": item["quantity"],
            "unit_price": item["unit_price"],
            "line_total": item["line_total"],
        }
        for item in available_items
    ]
    return {
        "items": items,
        "summary": {
            "total_amount": total_amount,
            "total_items": len(items),
            "total_quantity": total_quantity,
            "available_items": len(available_items),
            "has_unavailable_items": len(available_items) != len(items),
            "checkout_ready": bool(available_items) and len(available_items) == len(items),
            "currency": "RUB",
        },
        "checkout_payload": {
            "items": checkout_items,
            "total_amount": total_amount,
            "currency": "RUB",
        },
    }


def _empty_cart() -> dict:
    return _cart_payload([])
