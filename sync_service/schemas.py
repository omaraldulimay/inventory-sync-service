from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from sync_service.models import SyncMode


class OrmModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RunRequest(BaseModel):
    mode: SyncMode = SyncMode.INCREMENTAL
    dry_run: bool = False
    # A person confirms that the zero-stock updates are real, e.g. products really sold out.
    override_guard: bool = False


class RunOut(OrmModel):
    id: int
    mode: str
    trigger: str
    dry_run: bool
    status: str
    started_at: datetime
    finished_at: datetime | None
    duration_ms: int | None
    checked: int
    changed: int
    failed: int
    retries: int = 0
    error: str | None


class EventOut(OrmModel):
    id: int
    run_id: int
    sku: str
    action: str
    from_quantity: int | None
    to_quantity: int | None
    attempts: int
    duration_ms: int
    error: str | None
    created_at: datetime


class RunDetail(RunOut):
    events: list[EventOut]


class SkuStateOut(OrmModel):
    sku: str
    name: str | None
    erp_quantity: int | None
    shop_quantity: int | None
    erp_updated_at: datetime | None
    last_synced_at: datetime | None
    status: str
    last_error: str | None
    consecutive_failures: int


class SchedulerOut(BaseModel):
    enabled: bool
    paused: bool
    incremental_interval_seconds: float
    full_interval_seconds: float
    last_check_at: datetime | None


class SchedulerUpdate(BaseModel):
    paused: bool


class OverviewOut(BaseModel):
    total_skus: int
    sku_counts: dict[str, int]
    runs_24h: int
    failed_runs_24h: int
    retries_24h: int
    last_run: RunOut | None
    scheduler: SchedulerOut
    demo_mode: bool


class StockUpdate(BaseModel):
    quantity: int = Field(ge=0)


class ChaosSettings(BaseModel):
    failure_rate: float = Field(ge=0, le=1)
    latency_ms: int = Field(ge=0, le=10_000)
    rate_limit_requests: int = Field(ge=1, le=1000)
