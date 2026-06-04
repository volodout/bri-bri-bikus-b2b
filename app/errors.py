from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException


class CatalogError(Exception):
    def __init__(self, status_code: int, code: str, message: str):
        self.status_code = status_code
        self.code = code
        self.message = message


class B2BUnavailable(CatalogError):
    def __init__(self, message: str = "Catalog service temporarily unavailable"):
        super().__init__(502, "UPSTREAM_UNAVAILABLE", message)


class FavoritesB2BUnavailable(CatalogError):
    def __init__(self, message: str = "B2B service temporarily unavailable"):
        super().__init__(503, "B2B_UNAVAILABLE", message)


class InvalidRequest(CatalogError):
    def __init__(self, message: str):
        super().__init__(400, "INVALID_REQUEST", message)


class NotFound(CatalogError):
    def __init__(self, message: str = "Not found"):
        super().__init__(404, "NOT_FOUND", message)


class Unauthorized(CatalogError):
    def __init__(self, message: str = "Authorization required"):
        super().__init__(401, "UNAUTHORIZED", message)


class Forbidden(CatalogError):
    def __init__(self, message: str = "Forbidden"):
        super().__init__(403, "FORBIDDEN", message)


class Conflict(CatalogError):
    def __init__(self, code: str, message: str):
        super().__init__(409, code, message)


class ProductNotFound(CatalogError):
    def __init__(self, message: str = "Product not found"):
        super().__init__(404, "PRODUCT_NOT_FOUND", message)


class InvalidNotifyOn(CatalogError):
    def __init__(self, message: str = "Invalid notify_on"):
        super().__init__(400, "INVALID_NOTIFY_ON", message)


class MissingCartIdentity(CatalogError):
    def __init__(self, message: str = "Pass Authorization bearer token or X-Session-Id"):
        super().__init__(400, "MISSING_CART_IDENTITY", message)


class InvalidQuantity(CatalogError):
    def __init__(self, message: str = "Quantity must be at least 1"):
        super().__init__(400, "INVALID_QUANTITY", message)


class SkuNotFound(CatalogError):
    def __init__(self, message: str = "SKU not found"):
        super().__init__(404, "SKU_NOT_FOUND", message)


class SkuNotAvailable(CatalogError):
    def __init__(self, message: str = "SKU is not available"):
        super().__init__(410, "SKU_NOT_AVAILABLE", message)


class InsufficientStock(CatalogError):
    def __init__(self, message: str = "Insufficient stock"):
        super().__init__(422, "INSUFFICIENT_STOCK", message)


class CartItemNotFound(CatalogError):
    def __init__(self, message: str = "Cart item not found"):
        super().__init__(404, "CART_ITEM_NOT_FOUND", message)


class ServiceUnavailable(CatalogError):
    def __init__(self, message: str = "Service temporarily unavailable"):
        super().__init__(503, "SERVICE_UNAVAILABLE", message)


class EmptyBannerEvents(CatalogError):
    def __init__(self, message: str = "Events array must not be empty"):
        super().__init__(400, "EMPTY_EVENTS", message)


class InvalidBannerEventType(CatalogError):
    def __init__(self, message: str = "Allowed event values: impression, click"):
        super().__init__(400, "INVALID_EVENT_TYPE", message)


class BannerNotFound(CatalogError):
    def __init__(self, message: str = "Banner not found"):
        super().__init__(400, "BANNER_NOT_FOUND", message)


class OrphanCategoryHierarchy(CatalogError):
    def __init__(self, message: str = "Category hierarchy is broken"):
        super().__init__(422, "ORPHAN_NODE", message)


def _payload(code: str, message: str) -> dict:
    return {"code": code, "message": message}


async def catalog_error_handler(_: Request, exc: CatalogError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=_payload(exc.code, exc.message),
    )


# Maps HTTP status -> canonical `code` token. Every 4xx/5xx that the framework
# raises (unknown route, wrong method, validation, etc.) must surface through
# this map — never the framework default `{"detail": "..."}`.
_CODE_BY_STATUS: dict[int, str] = {
    400: "INVALID_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    422: "INVALID_REQUEST",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    502: "UPSTREAM_UNAVAILABLE",
    503: "UPSTREAM_UNAVAILABLE",
    504: "UPSTREAM_UNAVAILABLE",
}


def _code_for_status(status: int) -> str:
    if status in _CODE_BY_STATUS:
        return _CODE_BY_STATUS[status]
    if 400 <= status < 500:
        return "CLIENT_ERROR"
    if 500 <= status < 600:
        return "SERVER_ERROR"
    return "ERROR"


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) and exc.detail else "Error"
    return JSONResponse(
        status_code=exc.status_code,
        content=_payload(_code_for_status(exc.status_code), detail),
    )


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=_payload("INVALID_REQUEST", "Request validation failed"),
    )
