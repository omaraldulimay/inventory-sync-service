from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from sync_service.models import RunStatus, SkuState, SyncEvent, SyncRun
from sync_service.scheduler import SyncScheduler
from sync_service.schemas import (
    ChaosSettings,
    EventOut,
    OverviewOut,
    RunDetail,
    RunOut,
    RunRequest,
    SchedulerOut,
    SchedulerUpdate,
    SkuStateOut,
    StockUpdate,
)
from sync_service.sync import SyncAlreadyRunning, SyncEngine, utcnow

router = APIRouter(prefix="/api", tags=["Sync"])
demo_router = APIRouter(prefix="/api/demo", tags=["Demo"])


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.session_factory() as session:
        yield session


def get_engine(request: Request) -> SyncEngine:
    return request.app.state.engine


def get_scheduler(request: Request) -> SyncScheduler:
    return request.app.state.scheduler


SessionDep = Annotated[AsyncSession, Depends(get_session)]
EngineDep = Annotated[SyncEngine, Depends(get_engine)]
SchedulerDep = Annotated[SyncScheduler, Depends(get_scheduler)]


def scheduler_state(scheduler: SyncScheduler) -> SchedulerOut:
    return SchedulerOut(
        enabled=scheduler.running,
        paused=scheduler.paused,
        incremental_interval_seconds=scheduler.incremental_interval_seconds,
        full_interval_seconds=scheduler.full_interval_seconds,
        last_check_at=scheduler.engine.last_check_at,
    )


async def load_run(session: AsyncSession, run_id: int) -> SyncRun:
    run = await session.scalar(
        select(SyncRun).options(selectinload(SyncRun.events)).where(SyncRun.id == run_id)
    )
    if run is None:
        raise HTTPException(status_code=404, detail=f"Unknown run: {run_id}")
    return run


@router.get("/overview")
async def overview(request: Request, session: SessionDep, scheduler: SchedulerDep) -> OverviewOut:
    since = utcnow() - timedelta(hours=24)
    sku_counts = dict(
        (
            await session.execute(select(SkuState.status, func.count()).group_by(SkuState.status))
        ).all()
    )
    runs_24h, failed_runs_24h = (
        await session.execute(
            select(
                func.count(),
                func.count().filter(SyncRun.status.in_([RunStatus.FAILED, RunStatus.PARTIAL])),
            ).where(SyncRun.started_at >= since)
        )
    ).one()
    retries_24h = await session.scalar(
        select(func.coalesce(func.sum(SyncEvent.attempts - 1), 0)).where(
            SyncEvent.created_at >= since, SyncEvent.attempts > 1
        )
    )
    last_run = await session.scalar(select(SyncRun).order_by(SyncRun.id.desc()).limit(1))

    return OverviewOut(
        total_skus=sum(sku_counts.values()),
        sku_counts=sku_counts,
        runs_24h=runs_24h,
        failed_runs_24h=failed_runs_24h,
        retries_24h=retries_24h,
        last_run=RunOut.model_validate(last_run) if last_run else None,
        scheduler=scheduler_state(scheduler),
        demo_mode=request.app.state.settings.demo_mode,
    )


@router.get("/inventory")
async def inventory(session: SessionDep) -> list[SkuStateOut]:
    states = await session.scalars(select(SkuState).order_by(SkuState.sku))
    return [SkuStateOut.model_validate(state) for state in states]


@router.get("/runs")
async def list_runs(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 20
) -> list[RunOut]:
    runs = list(await session.scalars(select(SyncRun).order_by(SyncRun.id.desc()).limit(limit)))
    retries = dict(
        (
            await session.execute(
                select(SyncEvent.run_id, func.sum(SyncEvent.attempts - 1))
                .where(SyncEvent.run_id.in_([run.id for run in runs]), SyncEvent.attempts > 1)
                .group_by(SyncEvent.run_id)
            )
        ).all()
    )
    return [
        RunOut.model_validate(run).model_copy(update={"retries": retries.get(run.id, 0)})
        for run in runs
    ]


@router.get("/runs/{run_id}")
async def get_run(run_id: int, session: SessionDep) -> RunDetail:
    return RunDetail.model_validate(await load_run(session, run_id))


@router.post("/runs", status_code=201)
async def start_run(body: RunRequest, engine: EngineDep, session: SessionDep) -> RunDetail:
    try:
        run_id = await engine.run(
            body.mode,
            dry_run=body.dry_run,
            trigger="manual",
            override_guard=body.override_guard,
        )
    except SyncAlreadyRunning:
        raise HTTPException(status_code=409, detail="A sync run is already in progress") from None
    return RunDetail.model_validate(await load_run(session, run_id))


@router.get("/events")
async def list_events(
    session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 30
) -> list[EventOut]:
    events = await session.scalars(select(SyncEvent).order_by(SyncEvent.id.desc()).limit(limit))
    return [EventOut.model_validate(event) for event in events]


@router.get("/scheduler")
async def get_scheduler_state(scheduler: SchedulerDep) -> SchedulerOut:
    return scheduler_state(scheduler)


@router.put("/scheduler")
async def update_scheduler(body: SchedulerUpdate, scheduler: SchedulerDep) -> SchedulerOut:
    scheduler.paused = body.paused
    return scheduler_state(scheduler)


async def forward(
    http: httpx.AsyncClient, method: str, url: str, json: dict[str, Any] | None = None
) -> JSONResponse:
    try:
        response = await http.request(method, url, json=json)
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="Mock systems are not reachable") from None
    return JSONResponse(response.json(), status_code=response.status_code)


@demo_router.patch("/erp/{sku}")
async def change_erp_stock(sku: str, body: StockUpdate, request: Request) -> JSONResponse:
    return await forward(
        request.app.state.erp_http,
        "PATCH",
        f"/erp/products/{quote(sku, safe='')}",
        body.model_dump(),
    )


@demo_router.get("/chaos")
async def get_chaos(request: Request) -> JSONResponse:
    return await forward(request.app.state.shop_http, "GET", "/shop/admin/chaos")


@demo_router.put("/chaos")
async def set_chaos(body: ChaosSettings, request: Request) -> JSONResponse:
    return await forward(request.app.state.shop_http, "PUT", "/shop/admin/chaos", body.model_dump())
