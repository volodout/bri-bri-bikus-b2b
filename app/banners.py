from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol
from uuid import UUID, uuid4

from app.auth import optional_user_id_from_jwt
from app.errors import BannerNotFound, EmptyBannerEvents, InvalidBannerEventType, InvalidRequest


ALLOWED_BANNER_EVENTS = {"impression", "click"}


@dataclass(frozen=True)
class Banner:
    id: str
    title: str
    image_url: str
    link: str
    placement: str
    priority: int
    is_active: bool
    start_at: datetime | None
    end_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class BannerEvent:
    banner_id: str
    event: str
    timestamp: datetime


class BannerRepository(Protocol):
    async def list_active(self, now: datetime) -> list[Banner]: ...

    async def existing_banner_ids(self, banner_ids: set[str]) -> set[str]: ...

    async def save_events(self, user_id: str | None, events: list[BannerEvent]) -> None: ...

    async def aclose(self) -> None: ...


class InMemoryBannerRepository:
    def __init__(self, banners: list[Banner] | None = None) -> None:
        self._banners: dict[str, Banner] = {banner.id: banner for banner in banners or []}
        self.events: list[tuple[str | None, BannerEvent]] = []

    def add(self, banner: Banner) -> None:
        self._banners[banner.id] = banner

    async def list_active(self, now: datetime) -> list[Banner]:
        return sorted(
            [banner for banner in self._banners.values() if _is_active_banner(banner, now)],
            key=lambda banner: (banner.priority, banner.created_at, banner.id),
        )

    async def existing_banner_ids(self, banner_ids: set[str]) -> set[str]:
        return {banner_id for banner_id in banner_ids if banner_id in self._banners}

    async def save_events(self, user_id: str | None, events: list[BannerEvent]) -> None:
        for event in events:
            self.events.append((user_id, event))

    async def aclose(self) -> None:
        return None


class PostgresBannerRepository:
    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._pool: Any = None

    async def list_active(self, now: datetime) -> list[Banner]:
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id::text, title, image_url, link, placement, priority, is_active, start_at, end_at, created_at
                FROM banners
                WHERE is_active = true
                AND placement = 'home'
                AND (start_at IS NULL OR start_at <= $1)
                AND (end_at IS NULL OR end_at >= $1)
                ORDER BY priority ASC, created_at ASC, id ASC
                """,
                now,
            )
        return [_banner_from_row(row) for row in rows]

    async def existing_banner_ids(self, banner_ids: set[str]) -> set[str]:
        if not banner_ids:
            return set()
        pool = await self._get_pool()
        async with pool.acquire() as connection:
            rows = await connection.fetch(
                """
                SELECT id::text
                FROM banners
                WHERE id = ANY($1::uuid[])
                """,
                [UUID(banner_id) for banner_id in banner_ids],
            )
        return {str(row["id"]) for row in rows}

    async def save_events(self, user_id: str | None, events: list[BannerEvent]) -> None:
        if not events:
            return None
        pool = await self._get_pool()
        rows = [
            (
                uuid4(),
                UUID(event.banner_id),
                UUID(user_id) if user_id is not None else None,
                event.event,
                event.timestamp,
            )
            for event in events
        ]
        async with pool.acquire() as connection:
            await connection.executemany(
                """
                INSERT INTO banner_events (id, banner_id, user_id, event, timestamp)
                VALUES ($1, $2, $3, $4, $5)
                """,
                rows,
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


class BannerService:
    def __init__(self, repository: BannerRepository) -> None:
        self._repository = repository

    async def list_home_banners(self) -> dict:
        banners = await self._repository.list_active(datetime.now(timezone.utc))
        items = [_banner_payload(banner) for banner in banners]
        return {"items": items, "total_count": len(items)}

    async def record_events(self, user_id: str | None, events: list[BannerEvent]) -> None:
        if not events:
            raise EmptyBannerEvents()
        banner_ids = {event.banner_id for event in events}
        existing = await self._repository.existing_banner_ids(banner_ids)
        if existing != banner_ids:
            raise BannerNotFound()
        await self._repository.save_events(user_id, events)


def optional_banner_user_id_from_request(request: Any) -> str | None:
    return optional_user_id_from_jwt(request)


def banner_event_from_payload(payload: dict) -> BannerEvent:
    banner_id = _uuid_field(payload.get("banner_id"), "banner_id")
    event = payload.get("event")
    if event not in ALLOWED_BANNER_EVENTS:
        raise InvalidBannerEventType()
    timestamp = _timestamp_field(payload.get("timestamp"), "timestamp")
    return BannerEvent(banner_id=banner_id, event=event, timestamp=timestamp)


def _uuid_field(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise InvalidRequest(f"{field} must be a valid UUID")
    try:
        return str(UUID(value))
    except ValueError:
        raise InvalidRequest(f"{field} must be a valid UUID")


def _timestamp_field(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise InvalidRequest(f"{field} must be a valid ISO 8601 datetime")
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise InvalidRequest(f"{field} must be a valid ISO 8601 datetime")
    if timestamp.tzinfo is None:
        return timestamp.replace(tzinfo=timezone.utc)
    return timestamp.astimezone(timezone.utc)


def _is_active_banner(banner: Banner, now: datetime) -> bool:
    if not banner.is_active:
        return False
    if banner.placement != "home":
        return False
    if banner.start_at is not None and banner.start_at > now:
        return False
    if banner.end_at is not None and banner.end_at < now:
        return False
    return True


def _banner_payload(banner: Banner) -> dict:
    return {
        "id": banner.id,
        "title": banner.title,
        "image_url": banner.image_url,
        "link": banner.link,
        "priority": banner.priority,
    }


def _banner_from_row(row: Any) -> Banner:
    return Banner(
        id=str(row["id"]),
        title=row["title"],
        image_url=row["image_url"],
        link=row["link"],
        placement=row["placement"],
        priority=int(row["priority"]),
        is_active=bool(row["is_active"]),
        start_at=_parse_optional_datetime(row["start_at"]),
        end_at=_parse_optional_datetime(row["end_at"]),
        created_at=_parse_datetime(row["created_at"]),
    )


def _parse_optional_datetime(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    return _parse_datetime(value)


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
