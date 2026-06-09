from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.banners import BannerService, banner_event_from_payload, optional_banner_user_id_from_request
from app.errors import EmptyBannerEvents, InvalidRequest

router = APIRouter()


def get_banner_service(request: Request) -> BannerService:
    return request.app.state.banner_service


@router.get("/api/v1/catalog/banners")
async def get_catalog_banners(request: Request) -> dict:
    service = get_banner_service(request)
    return await service.list_catalog_banners()


@router.post("/api/v1/banner-events", status_code=204)
async def post_banner_events(request: Request) -> Response:
    body = await _json_body(request)
    raw_events = body.get("events")
    if not isinstance(raw_events, list):
        raise InvalidRequest("events must be an array")
    if not raw_events:
        raise EmptyBannerEvents()
    if len(raw_events) > 50:
        raise InvalidRequest("events must contain at most 50 items")
    events = []
    for raw_event in raw_events:
        if not isinstance(raw_event, dict):
            raise InvalidRequest("each event must be an object")
        events.append(banner_event_from_payload(raw_event))

    user_id = optional_banner_user_id_from_request(request)
    service = get_banner_service(request)
    await service.record_events(user_id, events)
    return Response(status_code=204)


async def _json_body(request: Request) -> dict:
    try:
        body = await request.json()
    except ValueError:
        raise InvalidRequest("Request body must be valid JSON")
    if not isinstance(body, dict):
        raise InvalidRequest("Request body must be an object")
    return body
