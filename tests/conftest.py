from __future__ import annotations

from typing import Callable
from urllib.parse import parse_qsl

import httpx
import pytest

from app.addresses import InMemoryAddressRepository
from app.b2b_client import B2BClient
from app.banners import InMemoryBannerRepository
from app.cart import InMemoryCartRepository
from app.collections import InMemoryCollectionRepository
from app.favorites import InMemoryFavoriteRepository
from app.main import create_app
from app.orders import InMemoryOrderRepository
from app.subscriptions import InMemoryProductSubscriptionRepository


HandlerFn = Callable[[httpx.Request], httpx.Response]


@pytest.fixture
def b2b_recorder():
    """Records every upstream call made by the B2B client.

    Tests append a handler and inspect captured requests after the fact.
    """

    captured: list[httpx.Request] = []
    handler_box: dict[str, HandlerFn] = {}

    def set_handler(fn: HandlerFn) -> None:
        handler_box["fn"] = fn

    def dispatch(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        fn = handler_box.get("fn")
        if fn is None:
            return httpx.Response(500, json={"message": "no handler set"})
        return fn(request)

    transport = httpx.MockTransport(dispatch)

    class Recorder:
        def __init__(self) -> None:
            self.requests = captured
            self.set_handler = set_handler
            self.transport = transport

        @property
        def last_query(self) -> list[tuple[str, str]]:
            assert self.requests, "no upstream request was made"
            url = self.requests[-1].url
            return parse_qsl(url.query.decode() if isinstance(url.query, bytes) else url.query, keep_blank_values=True)

    return Recorder()


@pytest.fixture
def banner_repository():
    return InMemoryBannerRepository()


@pytest.fixture
def collection_repository():
    return InMemoryCollectionRepository()


@pytest.fixture
def address_repository():
    return InMemoryAddressRepository()


@pytest.fixture
def order_repository():
    return InMemoryOrderRepository()


@pytest.fixture
def client(b2b_recorder, banner_repository, collection_repository, order_repository, address_repository):
    b2b = B2BClient(
        base_url="http://b2b.test",
        service_key="test-service-key",
        transport=b2b_recorder.transport,
    )
    app = create_app(
        b2b_client=b2b,
        favorite_repository=InMemoryFavoriteRepository(),
        subscription_repository=InMemoryProductSubscriptionRepository(),
        cart_repository=InMemoryCartRepository(),
        banner_repository=banner_repository,
        collection_repository=collection_repository,
        order_repository=order_repository,
        address_repository=address_repository,
    )
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://b2c.test")
