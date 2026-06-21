from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.errors import Conflict, InvalidRequest, Unauthorized
from app.product_events import ProductEventService
from app.query_parsing import validate_uuid

router = APIRouter()

_SUPPORTED_EVENTS = {
    "PRODUCT_BLOCKED",
    "PRODUCT_HARD_BLOCKED",
    "PRODUCT_DELETED",
    "SKU_OUT_OF_STOCK",
    "SKU_BACK_IN_STOCK",
    "PRICE_CHANGED",
}


@router.post("/api/v1/b2b/events")
async def handle_b2b_event(request: Request) -> JSONResponse:
    _require_service_key(request)

    try:
        body = await request.json()
    except ValueError:
        raise InvalidRequest("Request body must be valid JSON")
    if not isinstance(body, dict):
        raise InvalidRequest("Request body must be an object")

    event_type = body.get("event_type")
    idempotency_key = body.get("idempotency_key")

    if not isinstance(event_type, str) or event_type not in _SUPPORTED_EVENTS:
        raise InvalidRequest(
            f"event_type must be one of: {', '.join(sorted(_SUPPORTED_EVENTS))}"
        )
    if not isinstance(idempotency_key, str):
        raise InvalidRequest("idempotency_key is required")
    if validate_uuid(idempotency_key, field="idempotency_key") is None:
        raise InvalidRequest("idempotency_key must be a valid UUID")

    payload = body.get("payload") or {}
    if not isinstance(payload, dict):
        raise InvalidRequest("payload must be an object")

    service: ProductEventService = request.app.state.product_event_service
    is_new = await service.handle(event_type, idempotency_key, payload)

    if not is_new:
        raise Conflict("DUPLICATE_EVENT", "Event with this idempotency_key was already processed")

    return JSONResponse(status_code=202, content={"accepted": True})


def _require_service_key(request: Request) -> None:
    key = request.headers.get("X-Service-Key")
    if not key or key != settings.b2b_service_key:
        raise Unauthorized("Invalid or missing X-Service-Key")
