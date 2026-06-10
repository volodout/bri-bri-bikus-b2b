from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.banners import Banner


BANNER_ID = "550e8400-e29b-41d4-a716-446655440000"
SECOND_BANNER_ID = "6ba7b810-9dad-11d1-80b4-00c04fd430c8"
INACTIVE_BANNER_ID = "7c9e6679-7425-40de-944b-e07fc1f90ae7"


def banner(
    banner_id: str,
    *,
    priority: int,
    placement: str = "catalog",
    is_active: bool = True,
    start_at: datetime | None = None,
    end_at: datetime | None = None,
) -> Banner:
    now = datetime.now(timezone.utc)
    return Banner(
        id=banner_id,
        title=f"Banner {priority}",
        image_url=f"/cdn/banners/{banner_id}.jpg",
        link=f"/collections/{priority}",
        placement=placement,
        priority=priority,
        is_active=is_active,
        start_at=start_at,
        end_at=end_at,
        created_at=now + timedelta(seconds=priority),
    )


async def test_active_banners_returned_sorted_by_priority(client, banner_repository):
    now = datetime.now(timezone.utc)
    banner_repository.add(banner(BANNER_ID, priority=20, start_at=now - timedelta(days=1), end_at=now + timedelta(days=1)))
    banner_repository.add(
        banner(SECOND_BANNER_ID, priority=10, start_at=now - timedelta(days=1), end_at=now + timedelta(days=1))
    )
    banner_repository.add(banner(INACTIVE_BANNER_ID, priority=1, is_active=False))
    banner_repository.add(
        banner("8c9e6679-7425-40de-944b-e07fc1f90ae7", priority=2, start_at=now + timedelta(days=1))
    )
    banner_repository.add(
        banner("9c9e6679-7425-40de-944b-e07fc1f90ae7", priority=3, end_at=now - timedelta(days=1))
    )
    banner_repository.add(banner("10e66790-7425-40de-944b-e07fc1f90ae70", priority=1, placement="checkout"))

    async with client as ac:
        response = await ac.get("/api/v1/catalog/banners")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 2
    assert [item["id"] for item in body] == [SECOND_BANNER_ID, BANNER_ID]
    assert [item["ordering"] for item in body] == [10, 20]
    assert "is_active" not in body[0]


async def test_no_active_banners_returns_200_empty(client, banner_repository):
    now = datetime.now(timezone.utc)
    banner_repository.add(banner(BANNER_ID, priority=1, is_active=False))
    banner_repository.add(banner(SECOND_BANNER_ID, priority=2, start_at=now + timedelta(days=1)))

    async with client as ac:
        response = await ac.get("/api/v1/catalog/banners")

    assert response.status_code == 200
    assert response.json() == []


async def test_click_on_unknown_banner_returns_400(client):
    async with client as ac:
        response = await ac.post(
            "/api/v1/banner-events",
            json={
                "events": [
                    {
                        "banner_id": BANNER_ID,
                        "event": "click",
                        "timestamp": "2026-06-01T10:00:00Z",
                    }
                ]
            },
        )

    assert response.status_code == 400
    assert response.json()["code"] == "BANNER_NOT_FOUND"


async def test_banner_events_saved_for_known_banner(client, banner_repository):
    banner_repository.add(banner(BANNER_ID, priority=1))

    async with client as ac:
        response = await ac.post(
            "/api/v1/banner-events",
            json={
                "events": [
                    {
                        "banner_id": BANNER_ID,
                        "event": "impression",
                        "timestamp": "2026-06-01T10:00:00Z",
                    },
                    {
                        "banner_id": BANNER_ID,
                        "event": "click",
                        "timestamp": "2026-06-01T10:00:01Z",
                    },
                ]
            },
        )

    assert response.status_code == 204
    assert len(banner_repository.events) == 2
    assert [event.event for _, event in banner_repository.events] == ["impression", "click"]


async def test_empty_banner_events_returns_400(client):
    async with client as ac:
        response = await ac.post("/api/v1/banner-events", json={"events": []})

    assert response.status_code == 400
    assert response.json()["code"] == "EMPTY_EVENTS"
