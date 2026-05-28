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


class InvalidRequest(CatalogError):
    def __init__(self, message: str):
        super().__init__(400, "INVALID_REQUEST", message)


class NotFound(CatalogError):
    def __init__(self, message: str = "Not found"):
        super().__init__(404, "NOT_FOUND", message)


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
