from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.addresses import (
    Address,
    AddressRepository,
    address_from_snapshot,
    address_snapshot,
    to_address_response,
)
from app.b2b_client import B2BClient
from app.errors import (
    AddressNotFound,
    B2BUnavailable,
    EmptyOrderItems,
    InvalidOrderQuantity,
    NotFound,
    OrdersB2BUnavailable,
    ReserveFailed,
)


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    PAID = "PAID"
    ASSEMBLING = "ASSEMBLING"
    DELIVERING = "DELIVERING"
    DELIVERED = "DELIVERED"
    CANCELLED = "CANCELLED"
    CANCEL_PENDING = "CANCEL_PENDING"


@dataclass(frozen=True)
class OrderLine:
    sku_id: str
    quantity: int


@dataclass(frozen=True)
class OrderItem:
    id: str
    sku_id: str
    product_id: str
    product_title: str
    sku_name: str
    quantity: int
    unit_price: int
    line_total: int


@dataclass(frozen=True)
class Order:
    id: str
    user_id: str
    status: OrderStatus
    items: tuple[OrderItem, ...]
    total_amount: int
    address: Address
    payment_method_id: str
    comment: str | None
    idempotency_key: str
    created_at: datetime
    updated_at: datetime


class OrderRepository(Protocol):
    async def find_by_idempotency_key(self, idempotency_key: str) -> Order | None: ...

    async def create(self, order: Order) -> tuple[Order, bool]: ...

    async def aclose(self) -> None: ...


class InMemoryOrderRepository:
    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        self._by_key: dict[str, str] = {}

    async def find_by_idempotency_key(self, idempotency_key: str) -> Order | None:
        order_id = self._by_key.get(idempotency_key)
        return self._orders.get(order_id) if order_id is not None else None

    async def create(self, order: Order) -> tuple[Order, bool]:
        existing_id = self._by_key.get(order.idempotency_key)
        if existing_id is not None:
            return self._orders[existing_id], False
        self._orders[order.id] = order
        self._by_key[order.idempotency_key] = order.id
        return order, True

    async def aclose(self) -> None:
        return None


class PostgresOrderRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def find_by_idempotency_key(self, idempotency_key: str) -> Order | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            order_row = await connection.fetchrow(
                """
                SELECT id::text, user_id::text, status, total_amount,
                       address, payment_method_id::text, comment,
                       idempotency_key::text, created_at, updated_at
                FROM orders
                WHERE idempotency_key = $1
                """,
                UUID(idempotency_key),
            )
            if order_row is None:
                return None
            item_rows = await connection.fetch(
                """
                SELECT id::text, sku_id::text, product_id::text, product_title,
                       sku_name, quantity, unit_price, line_total
                FROM order_items
                WHERE order_id = $1
                ORDER BY position ASC
                """,
                UUID(order_row["id"]),
            )
        return _order_from_rows(order_row, item_rows)

    async def create(self, order: Order) -> tuple[Order, bool]:
        import asyncpg

        pool = await self._get_pool()
        async with pool.acquire() as connection:
            try:
                async with connection.transaction():
                    await connection.execute(
                        """
                        INSERT INTO orders (
                            id, user_id, status, total_amount, address,
                            payment_method_id, comment, idempotency_key,
                            created_at, updated_at
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        """,
                        UUID(order.id),
                        UUID(order.user_id),
                        order.status.value,
                        order.total_amount,
                        address_snapshot(order.address),
                        UUID(order.payment_method_id),
                        order.comment,
                        UUID(order.idempotency_key),
                        order.created_at,
                        order.updated_at,
                    )
                    for position, item in enumerate(order.items):
                        await connection.execute(
                            """
                            INSERT INTO order_items (
                                id, order_id, position, sku_id, product_id,
                                product_title, sku_name, quantity, unit_price, line_total
                            )
                            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                            """,
                            UUID(item.id),
                            UUID(order.id),
                            position,
                            UUID(item.sku_id),
                            UUID(item.product_id),
                            item.product_title,
                            item.sku_name,
                            item.quantity,
                            item.unit_price,
                            item.line_total,
                        )
            except asyncpg.UniqueViolationError:
                existing = await self.find_by_idempotency_key(order.idempotency_key)
                if existing is None:
                    raise
                return existing, False
        return order, True

    async def aclose(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def _get_pool(self):
        if self._pool is None:
            import asyncpg

            self._pool = await asyncpg.create_pool(dsn=self._database_url)
        return self._pool


class OrderService:
    def __init__(
        self,
        repository: OrderRepository,
        b2b_client: B2BClient,
        address_repository: AddressRepository,
    ) -> None:
        self._repository = repository
        self._b2b_client = b2b_client
        self._address_repository = address_repository

    async def create_order(
        self,
        user_id: str,
        idempotency_key: str,
        lines: list[OrderLine],
        address_id: str,
        payment_method_id: str,
        comment: str | None,
    ) -> tuple[Order, bool]:
        existing = await self._repository.find_by_idempotency_key(idempotency_key)
        if existing is not None:
            return existing, False

        _validate_lines(lines)

        address = await self._address_repository.get(address_id, user_id)
        if address is None:
            raise AddressNotFound()

        resolved: list[tuple[OrderLine, dict, dict]] = []
        failed: list[dict[str, Any]] = []
        for line in lines:
            product, sku = await self._resolve_sku(line.sku_id)
            reason = _failure_reason(product, sku, line.quantity)
            if reason is not None:
                failed.append(_failed_item(line, sku, reason))
            else:
                resolved.append((line, product, sku))
        if failed:
            raise ReserveFailed(failed)

        order_id = str(uuid4())
        outcome = await self._reserve(order_id, idempotency_key, lines)
        if not outcome.reserved:
            raise ReserveFailed(outcome.failed_items)

        order = _build_order(
            order_id, user_id, idempotency_key, address, payment_method_id, comment, resolved
        )
        return await self._repository.create(order)

    async def _resolve_sku(self, sku_id: str) -> tuple[dict, dict]:
        try:
            payload = await self._b2b_client.get_sku(sku_id)
        except NotFound:
            return {}, {}
        except B2BUnavailable as exc:
            raise OrdersB2BUnavailable(exc.message)
        product = payload.get("product")
        sku = payload.get("sku")
        if not isinstance(product, dict) or not isinstance(sku, dict):
            return {}, {}
        return product, sku

    async def _reserve(self, order_id: str, idempotency_key: str, lines: list[OrderLine]):
        try:
            return await self._b2b_client.reserve(
                idempotency_key,
                order_id,
                [{"sku_id": line.sku_id, "quantity": line.quantity} for line in lines],
            )
        except B2BUnavailable as exc:
            raise OrdersB2BUnavailable(exc.message)


def _validate_lines(lines: list[OrderLine]) -> None:
    if not lines:
        raise EmptyOrderItems()
    if any(line.quantity < 1 for line in lines):
        raise InvalidOrderQuantity()


def _failure_reason(product: dict, sku: dict, quantity: int) -> str | None:
    if not product or not sku:
        return "SKU_NOT_FOUND"
    if product.get("deleted") is True:
        return "PRODUCT_DELETED"
    status = product.get("status")
    if status in ("DELETED", "DELISTED"):
        return "PRODUCT_DELETED"
    if status in ("BLOCKED", "HARD_BLOCKED"):
        return "PRODUCT_BLOCKED"
    if status != "MODERATED":
        return "PRODUCT_BLOCKED"
    active_quantity = int(sku.get("active_quantity") or 0)
    if active_quantity <= 0:
        return "OUT_OF_STOCK"
    if active_quantity < quantity:
        return "INSUFFICIENT_STOCK"
    return None


def _failed_item(line: OrderLine, sku: dict, reason: str) -> dict[str, Any]:
    item: dict[str, Any] = {"sku_id": line.sku_id, "reason": reason}
    if reason in ("OUT_OF_STOCK", "INSUFFICIENT_STOCK"):
        item["requested"] = line.quantity
        item["available"] = int(sku.get("active_quantity") or 0)
    return item


def _build_order(
    order_id: str,
    user_id: str,
    idempotency_key: str,
    address: Address,
    payment_method_id: str,
    comment: str | None,
    resolved: list[tuple[OrderLine, dict, dict]],
) -> Order:
    now = datetime.now(timezone.utc)
    items: list[OrderItem] = []
    for line, product, sku in resolved:
        unit_price = int(sku.get("price") or 0)
        items.append(
            OrderItem(
                id=str(uuid4()),
                sku_id=line.sku_id,
                product_id=str(product.get("id") or ""),
                product_title=str(product.get("title") or ""),
                sku_name=str(sku.get("name") or ""),
                quantity=line.quantity,
                unit_price=unit_price,
                line_total=unit_price * line.quantity,
            )
        )
    return Order(
        id=order_id,
        user_id=user_id,
        status=OrderStatus.PAID,
        items=tuple(items),
        total_amount=sum(item.line_total for item in items),
        address=address,
        payment_method_id=payment_method_id,
        comment=comment,
        idempotency_key=idempotency_key,
        created_at=now,
        updated_at=now,
    )


def _order_from_rows(order_row: Any, item_rows: list[Any]) -> Order:
    return Order(
        id=str(order_row["id"]),
        user_id=str(order_row["user_id"]),
        status=OrderStatus(order_row["status"]),
        items=tuple(
            OrderItem(
                id=str(row["id"]),
                sku_id=str(row["sku_id"]),
                product_id=str(row["product_id"]),
                product_title=row["product_title"],
                sku_name=row["sku_name"],
                quantity=int(row["quantity"]),
                unit_price=int(row["unit_price"]),
                line_total=int(row["line_total"]),
            )
            for row in item_rows
        ),
        total_amount=int(order_row["total_amount"]),
        address=address_from_snapshot(order_row["address"]),
        payment_method_id=str(order_row["payment_method_id"]),
        comment=order_row["comment"],
        idempotency_key=str(order_row["idempotency_key"]),
        created_at=_parse_datetime(order_row["created_at"]),
        updated_at=_parse_datetime(order_row["updated_at"]),
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _item_name(item: OrderItem) -> str:
    if item.product_title and item.sku_name:
        return f"{item.product_title} – {item.sku_name}"
    return item.product_title or item.sku_name


def to_order_response(order: Order) -> dict[str, Any]:
    subtotal = sum(item.line_total for item in order.items)
    delivery_cost = 0
    return {
        "id": order.id,
        "buyer_id": order.user_id,
        "status": order.status.value,
        "items": [
            {
                "sku_id": item.sku_id,
                "product_id": item.product_id,
                "name": _item_name(item),
                "quantity": item.quantity,
                "unit_price": item.unit_price,
                "line_total": item.line_total,
            }
            for item in order.items
        ],
        "subtotal": subtotal,
        "delivery_cost": delivery_cost,
        "total": subtotal + delivery_cost,
        "address": to_address_response(order.address),
        "payment_method_id": order.payment_method_id,
        "comment": order.comment,
        "created_at": _iso(order.created_at),
        "updated_at": _iso(order.updated_at),
    }


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
