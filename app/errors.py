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


async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code_by_status = {400: "INVALID_REQUEST", 404: "NOT_FOUND", 502: "UPSTREAM_UNAVAILABLE", 503: "UPSTREAM_UNAVAILABLE"}
    code = code_by_status.get(exc.status_code, "ERROR")
    detail = exc.detail if isinstance(exc.detail, str) else "Error"
    return JSONResponse(status_code=exc.status_code, content=_payload(code, detail))


async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content=_payload("INVALID_REQUEST", "Request validation failed"),
    )
