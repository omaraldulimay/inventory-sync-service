from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from sync_service.db import Base, UtcDateTime


class SyncMode(StrEnum):
    INCREMENTAL = "incremental"
    FULL = "full"


class RunStatus(StrEnum):
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    PARTIAL = "partial"
    FAILED = "failed"
    BLOCKED = "blocked"
    ABORTED = "aborted"


class SkuStatus(StrEnum):
    IN_SYNC = "in_sync"
    FAILED = "failed"
    BLOCKED = "blocked"
    MISSING_IN_SHOP = "missing_in_shop"
    UNMAPPED = "unmapped"


class EventAction(StrEnum):
    UPDATED = "updated"
    PLANNED = "planned"
    FAILED = "failed"
    BLOCKED = "blocked"
    MISSING_IN_SHOP = "missing_in_shop"
    UNMAPPED = "unmapped"


class SyncRun(Base):
    __tablename__ = "sync_runs"
    __table_args__ = (
        # The database guarantees that at most one run is in progress at any time.
        Index(
            "ix_sync_runs_single_running",
            "status",
            unique=True,
            postgresql_where=text("status = 'running'"),
            sqlite_where=text("status = 'running'"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    mode: Mapped[str] = mapped_column(String(20))
    trigger: Mapped[str] = mapped_column(String(20))
    dry_run: Mapped[bool] = mapped_column(default=False)
    status: Mapped[str] = mapped_column(String(20))
    started_at: Mapped[datetime] = mapped_column(UtcDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    checked: Mapped[int] = mapped_column(default=0)
    changed: Mapped[int] = mapped_column(default=0)
    failed: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)

    events: Mapped[list["SyncEvent"]] = relationship(
        back_populates="run", order_by="SyncEvent.id", cascade="all, delete-orphan"
    )

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return round((self.finished_at - self.started_at).total_seconds() * 1000)


class SkuState(Base):
    __tablename__ = "sku_states"

    sku: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str | None] = mapped_column(String(200))
    erp_quantity: Mapped[int | None]
    shop_quantity: Mapped[int | None]
    erp_updated_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    last_synced_at: Mapped[datetime | None] = mapped_column(UtcDateTime())
    status: Mapped[str] = mapped_column(String(20))
    last_error: Mapped[str | None] = mapped_column(Text)
    consecutive_failures: Mapped[int] = mapped_column(default=0)


class SyncEvent(Base):
    __tablename__ = "sync_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("sync_runs.id"), index=True)
    sku: Mapped[str] = mapped_column(String(64), index=True)
    action: Mapped[str] = mapped_column(String(20))
    from_quantity: Mapped[int | None]
    to_quantity: Mapped[int | None]
    attempts: Mapped[int] = mapped_column(default=0)
    duration_ms: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UtcDateTime(), index=True)

    run: Mapped[SyncRun] = relationship(back_populates="events")
