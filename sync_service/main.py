import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from sync_service.api import demo_router, router
from sync_service.clients import ErpClient, ShopClient
from sync_service.config import Settings
from sync_service.db import create_database, create_tables
from sync_service.http import RetryPolicy, Sleep, Throttle
from sync_service.scheduler import SyncScheduler
from sync_service.sync import SyncEngine

DASHBOARD_DIR = Path(__file__).resolve().parent.parent / "dashboard"

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def create_app(
    settings: Settings | None = None,
    *,
    erp_transport: httpx.AsyncBaseTransport | None = None,
    shop_transport: httpx.AsyncBaseTransport | None = None,
    sleep: Sleep = asyncio.sleep,
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_engine, session_factory = create_database(settings.database_url)
        await create_tables(db_engine)

        timeout = httpx.Timeout(settings.request_timeout_seconds)
        erp_http = httpx.AsyncClient(
            base_url=settings.erp_base_url, timeout=timeout, transport=erp_transport
        )
        shop_http = httpx.AsyncClient(
            base_url=settings.shop_base_url, timeout=timeout, transport=shop_transport
        )
        policy = RetryPolicy(
            max_attempts=settings.retry_max_attempts,
            base_delay_seconds=settings.retry_base_delay_seconds,
            max_delay_seconds=settings.retry_max_delay_seconds,
        )
        throttle = Throttle(settings.shop_requests_per_second, sleep=sleep)
        engine = SyncEngine(
            session_factory,
            ErpClient(erp_http, policy, sleep=sleep),
            ShopClient(shop_http, policy, throttle, sleep=sleep),
            settings,
        )
        await engine.recover_interrupted_runs()

        scheduler = SyncScheduler(
            engine, settings.incremental_interval_seconds, settings.full_interval_seconds
        )
        if settings.scheduler_enabled:
            scheduler.start()

        app.state.settings = settings
        app.state.session_factory = session_factory
        app.state.engine = engine
        app.state.scheduler = scheduler
        app.state.erp_http = erp_http
        app.state.shop_http = shop_http
        try:
            yield
        finally:
            await scheduler.stop()
            await erp_http.aclose()
            await shop_http.aclose()
            await db_engine.dispose()

    app = FastAPI(title="Inventory Sync Service", lifespan=lifespan)
    app.include_router(router)
    if settings.demo_mode:
        app.include_router(demo_router)

    @app.get("/health", tags=["System"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.mount("/", StaticFiles(directory=DASHBOARD_DIR, html=True), name="dashboard")
    return app


app = create_app()
