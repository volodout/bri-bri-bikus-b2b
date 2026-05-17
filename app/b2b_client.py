from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlencode

import httpx

from app.config import settings
from app.errors import B2BUnavailable, InvalidRequest, NotFound


class B2BClient:
    """HTTP client to the B2B service.

    Visibility (status=MODERATED, deleted=false, active_quantity>0) is enforced
    by B2B itself; B2C only proxies the request with the service key.
    """

    def __init__(
        self,
        base_url: str | None = None,
        service_key: str | None = None,
        timeout: float | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._base_url = (base_url or settings.b2b_base_url).rstrip("/")
        self._service_key = service_key or settings.b2b_service_key
        self._timeout = timeout or settings.b2b_timeout_seconds
        self._transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={"X-Service-Key": self._service_key},
            transport=self._transport,
        )

    async def _get(self, path: str, params: list[tuple[str, str]] | Mapping[str, Any]) -> dict:
        try:
            async with self._client() as client:
                response = await client.get(path, params=params)
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, httpx.NetworkError):
            raise B2BUnavailable()
        except httpx.HTTPError as exc:
            raise B2BUnavailable(f"Upstream transport error: {exc.__class__.__name__}")

        status = response.status_code
        if 200 <= status < 300:
            return response.json()
        if status == 400:
            payload = _safe_json(response)
            raise InvalidRequest(_extract_message(payload, "Invalid request"))
        if status == 404:
            payload = _safe_json(response)
            raise NotFound(_extract_message(payload, "Not found"))
        if status in (502, 503, 504):
            raise B2BUnavailable()
        raise B2BUnavailable(f"Unexpected upstream status: {status}")

    async def list_products(self, query: list[tuple[str, str]]) -> dict:
        return await self._get("/api/v1/products", query)

    async def get_facets(self, query: list[tuple[str, str]]) -> dict:
        return await self._get("/api/v1/catalog/facets", query)


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _extract_message(payload: dict, default: str) -> str:
    return str(payload.get("message") or payload.get("error") or default)
