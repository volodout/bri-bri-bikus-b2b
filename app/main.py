from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.addresses import AddressRepository, PostgresAddressRepository
from app.b2b_client import B2BClient
from app.banners import BannerRepository, BannerService, PostgresBannerRepository
from app.cart import CartRepository, CartService, PostgresCartRepository
from app.collections import CollectionRepository, CollectionService, PostgresCollectionRepository
from app.errors import (
    CatalogError,
    catalog_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.config import settings
from app.favorites import FavoriteRepository, FavoriteService, PostgresFavoriteRepository
from app.orders import OrderRepository, OrderService, PostgresOrderRepository
from app.subscriptions import (
    PostgresProductSubscriptionRepository,
    ProductSubscriptionRepository,
    ProductSubscriptionService,
)
from app.routes import banners, cart, categories, collections, facets, favorites, orders, products


def create_app(
    b2b_client: B2BClient | None = None,
    favorite_repository: FavoriteRepository | None = None,
    subscription_repository: ProductSubscriptionRepository | None = None,
    cart_repository: CartRepository | None = None,
    banner_repository: BannerRepository | None = None,
    collection_repository: CollectionRepository | None = None,
    order_repository: OrderRepository | None = None,
    address_repository: AddressRepository | None = None,
) -> FastAPI:
    client = b2b_client or B2BClient()
    favorites_repo = favorite_repository or PostgresFavoriteRepository(settings.database_url)
    subscriptions_repo = subscription_repository or PostgresProductSubscriptionRepository(settings.database_url)
    cart_repo = cart_repository or PostgresCartRepository(settings.database_url)
    banners_repo = banner_repository or PostgresBannerRepository(settings.database_url)
    collections_repo = collection_repository or PostgresCollectionRepository(settings.database_url)
    orders_repo = order_repository or PostgresOrderRepository(settings.database_url)
    addresses_repo = address_repository or PostgresAddressRepository(settings.database_url)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            yield
        finally:
            await subscriptions_repo.aclose()
            await favorites_repo.aclose()
            await cart_repo.aclose()
            await banners_repo.aclose()
            await collections_repo.aclose()
            await orders_repo.aclose()
            await addresses_repo.aclose()
            await client.aclose()

    app = FastAPI(
        title="NeoMarket B2C Catalog",
        version="0.1.0",
        description="B2C catalog service. Proxies catalog queries to B2B.",
        lifespan=lifespan,
    )
    app.state.b2b_client = client
    app.state.favorite_repository = favorites_repo
    app.state.favorite_service = FavoriteService(favorites_repo, client)
    app.state.subscription_repository = subscriptions_repo
    app.state.subscription_service = ProductSubscriptionService(subscriptions_repo, client)
    app.state.cart_repository = cart_repo
    app.state.cart_service = CartService(cart_repo, client)
    app.state.banner_repository = banners_repo
    app.state.banner_service = BannerService(banners_repo)
    app.state.collection_repository = collections_repo
    app.state.collection_service = CollectionService(collections_repo, client)
    app.state.order_repository = orders_repo
    app.state.address_repository = addresses_repo
    app.state.order_service = OrderService(orders_repo, client, addresses_repo, cart_repo)

    app.add_exception_handler(CatalogError, catalog_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)

    app.include_router(products.router)
    app.include_router(facets.router)
    app.include_router(categories.router)
    app.include_router(favorites.router)
    app.include_router(cart.router)
    app.include_router(banners.router)
    app.include_router(collections.router)
    app.include_router(orders.router)
    return app


app = create_app()
