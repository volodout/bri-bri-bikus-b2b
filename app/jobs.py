from __future__ import annotations

import asyncio
import logging

from app.b2b_client import B2BClient
from app.config import settings
from app.orders import PostgresOrderRepository, retry_pending_cancellations, retry_pending_fulfillments

logger = logging.getLogger(__name__)


async def run() -> int:
    repository = PostgresOrderRepository(settings.database_url)
    client = B2BClient()
    try:
        cancelled = await retry_pending_cancellations(repository, client)
    finally:
        await repository.aclose()
        await client.aclose()
    logger.info("cancel_pending retry finished", extra={"cancelled": cancelled})
    return cancelled


async def run_fulfill_retry() -> int:
    repository = PostgresOrderRepository(settings.database_url)
    client = B2BClient()
    try:
        fulfilled = await retry_pending_fulfillments(repository, client)
    finally:
        await repository.aclose()
        await client.aclose()
    logger.info("fulfill retry finished", extra={"fulfilled": fulfilled})
    return fulfilled


if __name__ == "__main__":
    asyncio.run(run())
