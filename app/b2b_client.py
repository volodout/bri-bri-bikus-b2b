from __future__ import annotations

from typing import Any, Mapping

import httpx

from app.config import settings
from app.errors import B2BUnavailable, InvalidRequest, NotFound


class B2BClient:
    """HTTP client to the B2B service.

    Visibility (status=MODERATED, deleted=false, active_quantity>0) is enforced
    by B2B itself; B2C only proxies the request with the service key.

    A single underlying ``httpx.AsyncClient`` is reused across calls to keep
    connections warm (HTTP keep-alive and pool reuse). The app's lifespan
    closes it on shutdown via :meth:`aclose`.
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
        self._async_client: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"X-Service-Key": self._service_key},
                transport=self._transport,
            )
        return self._async_client

    async def aclose(self) -> None:
        if self._async_client is not None:
            await self._async_client.aclose()
            self._async_client = None

    async def _get(self, path: str, params: list[tuple[str, str]] | Mapping[str, Any]) -> dict:
        client = self._client()
        try:
            response = await client.get(path, params=params)
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException, httpx.NetworkError):
            raise B2BUnavailable()
        except httpx.HTTPError as exc:
            raise B2BUnavailable(f"Upstream transport error: {exc.__class__.__name__}")

        status = response.status_code
        if 200 <= status < 300:
            try:
                return response.json()
            except ValueError:
                # Upstream violated its contract: 2xx with non-JSON body.
                # Surface as 502 so the public API keeps the {code,message} shape.
                raise B2BUnavailable("Upstream returned invalid JSON")
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

    async def list_products_by_ids(self, product_ids: list[str]) -> dict:
        return await self._get("/api/v1/products", [("ids", ",".join(product_ids))])

    async def get_product(self, product_id: str) -> dict:
        return await self._get(f"/api/v1/products/{product_id}", ())

    async def get_similar_products(
        self,
        product_id: str,
        query: list[tuple[str, str]],
    ) -> dict:
        return await self._get(f"/api/v1/products/{product_id}/similar", query)

    async def get_facets(self, query: list[tuple[str, str]]) -> dict:
        return await self._get("/api/v1/catalog/facets", query)

    async def list_categories(self) -> dict:
        return await self._get("/api/v1/categories", ())

    async def get_category(self, category_id: str, *, include_product_count: bool) -> dict:
        params: list[tuple[str, str]] = []
        if include_product_count:
            params.append(("include_product_count", "true"))
        return await self._get(f"/api/v1/categories/{category_id}", params)

    async def get_breadcrumbs(self, query: list[tuple[str, str]]) -> dict:
        return await self._get("/api/v1/breadcrumbs", query)


def _safe_json(response: httpx.Response) -> dict:
    try:
        data = response.json()
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


def _extract_message(payload: dict, default: str) -> str:
    return str(payload.get("message") or payload.get("error") or default)
