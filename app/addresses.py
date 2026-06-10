from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True)
class Address:
    id: str
    country: str
    city: str
    street: str
    building: str
    is_default: bool
    created_at: datetime
    region: str | None = None
    apartment: str | None = None
    postal_code: str | None = None
    recipient_name: str | None = None
    recipient_phone: str | None = None
    comment: str | None = None


class AddressRepository(Protocol):
    async def get(self, address_id: str, user_id: str) -> Address | None: ...

    async def aclose(self) -> None: ...


class InMemoryAddressRepository:
    def __init__(self) -> None:
        self._addresses: dict[tuple[str, str], Address] = {}

    def add(self, user_id: str, address: Address) -> None:
        self._addresses[(address.id, user_id)] = address

    async def get(self, address_id: str, user_id: str) -> Address | None:
        return self._addresses.get((address_id, user_id))

    async def aclose(self) -> None:
        return None


class PostgresAddressRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def get(self, address_id: str, user_id: str) -> Address | None:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            row = await connection.fetchrow(
                """
                SELECT id::text, country, region, city, street, building, apartment,
                       postal_code, recipient_name, recipient_phone, is_default,
                       comment, created_at
                FROM addresses
                WHERE id = $1 AND user_id = $2
                """,
                UUID(address_id),
                UUID(user_id),
            )
        if row is None:
            return None
        return Address(
            id=str(row["id"]),
            country=row["country"],
            region=row["region"],
            city=row["city"],
            street=row["street"],
            building=row["building"],
            apartment=row["apartment"],
            postal_code=row["postal_code"],
            recipient_name=row["recipient_name"],
            recipient_phone=row["recipient_phone"],
            is_default=bool(row["is_default"]),
            comment=row["comment"],
            created_at=_parse_datetime(row["created_at"]),
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


def to_address_response(address: Address) -> dict[str, Any]:
    out: dict[str, Any] = {
        "id": address.id,
        "country": address.country,
        "city": address.city,
        "street": address.street,
        "building": address.building,
        "is_default": address.is_default,
        "created_at": _iso(address.created_at),
    }
    optional = {
        "region": address.region,
        "apartment": address.apartment,
        "postal_code": address.postal_code,
        "recipient_name": address.recipient_name,
        "recipient_phone": address.recipient_phone,
        "comment": address.comment,
    }
    out.update({key: value for key, value in optional.items() if value is not None})
    return out


def address_snapshot(address: Address) -> str:
    return json.dumps(to_address_response(address))


def address_from_snapshot(raw: str | dict[str, Any]) -> Address:
    data = json.loads(raw) if isinstance(raw, str) else raw
    return Address(
        id=data["id"],
        country=data["country"],
        city=data["city"],
        street=data["street"],
        building=data["building"],
        is_default=bool(data.get("is_default", False)),
        created_at=_parse_datetime(data["created_at"]),
        region=data.get("region"),
        apartment=data.get("apartment"),
        postal_code=data.get("postal_code"),
        recipient_name=data.get("recipient_name"),
        recipient_phone=data.get("recipient_phone"),
        comment=data.get("comment"),
    )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
