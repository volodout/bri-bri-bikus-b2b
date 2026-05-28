from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.b2b_client import B2BClient
from app.errors import (
    CatalogError,
    catalog_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.routes import categories, facets, products


def create_app(b2b_client: B2BClient | None = None) -> FastAPI:
    client = b2b_client or B2BClient()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await client.aclose()

    app = FastAPI(
        title="NeoMarket B2C Catalog",
        version="0.1.0",
        description="B2C catalog service. Proxies catalog queries to B2B.",
        lifespan=lifespan,
    )
    app.state.b2b_client = client

    app.add_exception_handler(CatalogError, catalog_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(products.router)
    app.include_router(facets.router)
    app.include_router(categories.router)
    return app


app = create_app()
