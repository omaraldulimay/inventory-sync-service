import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from sync_service.clients import ErpClient, ShopClient, SkuNotFound, StockLevel, UnexpectedResponse
from sync_service.config import Settings
from sync_service.http import RetriesExhausted
from sync_service.models import (
    EventAction,
    RunStatus,
    SkuState,
    SkuStatus,
    SyncEvent,
    SyncMode,
    SyncRun,
)

logger = logging.getLogger(__name__)


class SyncAlreadyRunning(Exception):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass
class SkuSnapshot:
    sku: str
    name: str | None
    erp: StockLevel | None
    shop_quantity: int | None
    # True/False when we know whether the shop lists the SKU, None when only a write can tell.
    in_shop: bool | None

    @property
    def needs_write(self) -> bool:
        return (
            self.erp is not None
            and self.in_shop is not False
            and self.shop_quantity != self.erp.quantity
        )


@dataclass
class WriteOutcome:
    snapshot: SkuSnapshot
    action: EventAction
    attempts: int = 0
    duration_ms: int = 0
    error: str | None = None


def run_status(outcomes: list[WriteOutcome]) -> RunStatus:
    failed = sum(outcome.action is EventAction.FAILED for outcome in outcomes)
    if failed == 0:
        return RunStatus.SUCCEEDED
    return RunStatus.PARTIAL if failed < len(outcomes) else RunStatus.FAILED


def describe_error(error: Exception) -> str:
    message = str(error) or type(error).__name__
    return message[:500]


class SyncEngine:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        erp: ErpClient,
        shop: ShopClient,
        settings: Settings,
    ) -> None:
        self.session_factory = session_factory
        self.erp = erp
        self.shop = shop
        self.settings = settings
        self.last_check_at: datetime | None = None
        self._write_slots = asyncio.Semaphore(settings.max_concurrency)

    async def recover_interrupted_runs(self) -> None:
        async with self.session_factory() as session:
            await session.execute(
                update(SyncRun)
                .where(SyncRun.status == RunStatus.RUNNING)
                .values(
                    status=RunStatus.ABORTED,
                    finished_at=utcnow(),
                    error="Service was restarted during the run",
                )
            )
            await session.commit()

    async def run(
        self,
        mode: SyncMode,
        *,
        dry_run: bool = False,
        trigger: str = "manual",
        override_guard: bool = False,
    ) -> int:
        run_id = await self._start_run(mode, dry_run, trigger)
        try:
            await self._sync(run_id, mode, dry_run, trigger, override_guard)
        except asyncio.CancelledError:
            await self._end_run(run_id, RunStatus.ABORTED, "Run was cancelled")
            raise
        except Exception as error:
            logger.exception("Sync run %d failed", run_id)
            await self._end_run(run_id, RunStatus.FAILED, describe_error(error))
        finally:
            self.last_check_at = utcnow()
        return run_id

    async def _start_run(self, mode: SyncMode, dry_run: bool, trigger: str) -> int:
        async with self.session_factory() as session:
            run = SyncRun(
                mode=mode,
                trigger=trigger,
                dry_run=dry_run,
                status=RunStatus.RUNNING,
                started_at=utcnow(),
            )
            session.add(run)
            try:
                await session.commit()
            except IntegrityError:
                raise SyncAlreadyRunning from None
            return run.id

    async def _sync(
        self, run_id: int, mode: SyncMode, dry_run: bool, trigger: str, override_guard: bool
    ) -> None:
        async with self.session_factory() as session:
            states = {state.sku: state for state in await session.scalars(select(SkuState))}

        if mode is SyncMode.FULL or not states:
            snapshots = await self._observe_everything()
        else:
            snapshots = await self._observe_changes(states)
        to_write = [snapshot for snapshot in snapshots if snapshot.needs_write]

        error = None
        if dry_run:
            outcomes = [WriteOutcome(snapshot, EventAction.PLANNED) for snapshot in to_write]
            status = RunStatus.SUCCEEDED
        elif not override_guard and (guard_message := self._zero_stock_guard(to_write)) is not None:
            outcomes = [WriteOutcome(snapshot, EventAction.BLOCKED) for snapshot in to_write]
            status = RunStatus.BLOCKED
            error = guard_message
        else:
            outcomes = list(await asyncio.gather(*(self._write(s) for s in to_write)))
            status = run_status(outcomes)

        await self._record(run_id, trigger, dry_run, snapshots, outcomes, status, error)

    async def _observe_everything(self) -> list[SkuSnapshot]:
        erp_levels, shop_levels = await asyncio.gather(
            self.erp.fetch_stock(), self.shop.fetch_stock()
        )
        erp_by_sku = {level.sku: level for level in erp_levels}
        shop_by_sku = {level.sku: level for level in shop_levels}

        snapshots = []
        for sku in sorted(erp_by_sku.keys() | shop_by_sku.keys()):
            erp = erp_by_sku.get(sku)
            shop = shop_by_sku.get(sku)
            snapshots.append(
                SkuSnapshot(
                    sku=sku,
                    name=erp.name if erp else shop.name,
                    erp=erp,
                    shop_quantity=shop.quantity if shop else None,
                    in_shop=shop is not None,
                )
            )
        return snapshots

    async def _observe_changes(self, states: dict[str, SkuState]) -> list[SkuSnapshot]:
        # Ask the ERP only for what changed since the newest change we know about. Using ">="
        # means boundary items come back again; that is harmless because unchanged stock is skipped.
        cursor = max(
            (state.erp_updated_at for state in states.values() if state.erp_updated_at),
            default=None,
        )
        changed = {level.sku: level for level in await self.erp.fetch_stock(updated_since=cursor)}

        # Failed writes are retried even if the ERP did not change again, or they would be lost.
        for state in states.values():
            if (
                state.status == SkuStatus.FAILED
                and state.sku not in changed
                and state.erp_quantity is not None
            ):
                changed[state.sku] = StockLevel(
                    sku=state.sku,
                    quantity=state.erp_quantity,
                    name=state.name,
                    updated_at=state.erp_updated_at,
                )

        snapshots = []
        for sku in sorted(changed):
            erp = changed[sku]
            state = states.get(sku)
            if state is None or state.status == SkuStatus.FAILED:
                shop_quantity, in_shop = None, None
            elif state.status == SkuStatus.MISSING_IN_SHOP:
                shop_quantity, in_shop = None, False
            else:
                shop_quantity, in_shop = state.shop_quantity, True
            snapshots.append(SkuSnapshot(sku, erp.name, erp, shop_quantity, in_shop))
        return snapshots

    def _zero_stock_guard(self, to_write: list[SkuSnapshot]) -> str | None:
        # A broken ERP export that reports 0 everywhere must not empty the shop.
        if len(to_write) < self.settings.zero_stock_guard_min_items:
            return None
        zeroing = sum(
            1 for snapshot in to_write if snapshot.erp.quantity == 0 and snapshot.shop_quantity != 0
        )
        if zeroing / len(to_write) <= self.settings.zero_stock_guard_ratio:
            return None
        return (
            f"Zero-stock guard: {zeroing} of {len(to_write)} updates would set stock to 0. "
            "Nothing was written, please check the ERP data."
        )

    async def _write(self, snapshot: SkuSnapshot) -> WriteOutcome:
        async with self._write_slots:
            started = time.perf_counter()
            try:
                attempts = await self.shop.set_stock(snapshot.sku, snapshot.erp.quantity)
                outcome = WriteOutcome(snapshot, EventAction.UPDATED, attempts)
            except SkuNotFound as error:
                outcome = WriteOutcome(snapshot, EventAction.MISSING_IN_SHOP, error.attempts)
            except RetriesExhausted as error:
                outcome = WriteOutcome(
                    snapshot, EventAction.FAILED, error.attempts, error=error.reason
                )
            except (UnexpectedResponse, httpx.HTTPError) as error:
                outcome = WriteOutcome(snapshot, EventAction.FAILED, 1, error=describe_error(error))
            outcome.duration_ms = round((time.perf_counter() - started) * 1000)
            return outcome

    async def _record(
        self,
        run_id: int,
        trigger: str,
        dry_run: bool,
        snapshots: list[SkuSnapshot],
        outcomes: list[WriteOutcome],
        status: RunStatus,
        error: str | None,
    ) -> None:
        now = utcnow()
        outcome_by_sku = {outcome.snapshot.sku: outcome for outcome in outcomes}
        events = [
            SyncEvent(
                run_id=run_id,
                sku=outcome.snapshot.sku,
                action=outcome.action,
                from_quantity=outcome.snapshot.shop_quantity,
                to_quantity=outcome.snapshot.erp.quantity,
                attempts=outcome.attempts,
                duration_ms=outcome.duration_ms,
                error=outcome.error,
                created_at=now,
            )
            for outcome in outcomes
        ]

        async with self.session_factory() as session:
            run = await session.get(SyncRun, run_id)

            if not dry_run:
                skus = [snapshot.sku for snapshot in snapshots]
                states = {
                    state.sku: state
                    for state in await session.scalars(
                        select(SkuState).where(SkuState.sku.in_(skus))
                    )
                }
                for snapshot in snapshots:
                    state = states.get(snapshot.sku)
                    if state is None:
                        state = SkuState(sku=snapshot.sku, consecutive_failures=0)
                        session.add(state)
                    previous_status = state.status
                    outcome = outcome_by_sku.get(snapshot.sku)
                    apply_to_state(state, snapshot, outcome, now)

                    # Report a SKU without a counterpart once, not on every run.
                    if (
                        outcome is None
                        and state.status != previous_status
                        and state.status
                        in (
                            SkuStatus.MISSING_IN_SHOP,
                            SkuStatus.UNMAPPED,
                        )
                    ):
                        events.append(
                            SyncEvent(
                                run_id=run_id,
                                sku=snapshot.sku,
                                action=state.status,
                                from_quantity=snapshot.shop_quantity,
                                to_quantity=snapshot.erp.quantity if snapshot.erp else None,
                                created_at=now,
                            )
                        )

            # Scheduled runs that found nothing to do would only flood the history.
            if trigger == "schedule" and not events and status is RunStatus.SUCCEEDED:
                await session.delete(run)
            else:
                run.status = status
                run.finished_at = now
                run.checked = len(snapshots)
                run.changed = sum(
                    outcome.action in (EventAction.UPDATED, EventAction.PLANNED)
                    for outcome in outcomes
                )
                run.failed = sum(outcome.action is EventAction.FAILED for outcome in outcomes)
                run.error = error
                session.add_all(events)
                logger.info(
                    "Sync run %d (%s%s) finished: %s, %d changed, %d failed",
                    run_id,
                    run.mode,
                    ", dry run" if dry_run else "",
                    status,
                    run.changed,
                    run.failed,
                )
            await session.commit()

    async def _end_run(self, run_id: int, status: RunStatus, error: str) -> None:
        async with self.session_factory() as session:
            run = await session.get(SyncRun, run_id)
            if run is None:
                return
            run.status = status
            run.finished_at = utcnow()
            run.error = error
            await session.commit()


def apply_to_state(
    state: SkuState, snapshot: SkuSnapshot, outcome: WriteOutcome | None, now: datetime
) -> None:
    state.name = snapshot.name or state.name
    if snapshot.erp is not None:
        state.erp_quantity = snapshot.erp.quantity
        state.erp_updated_at = snapshot.erp.updated_at
    else:
        state.erp_quantity = None
        state.erp_updated_at = None
    if snapshot.in_shop is not None:
        state.shop_quantity = snapshot.shop_quantity

    if outcome is None:
        if snapshot.erp is None:
            state.status = SkuStatus.UNMAPPED
        elif snapshot.in_shop is False:
            state.status = SkuStatus.MISSING_IN_SHOP
        else:
            state.status = SkuStatus.IN_SYNC
        state.last_error = None
        state.consecutive_failures = 0
    elif outcome.action is EventAction.UPDATED:
        state.shop_quantity = snapshot.erp.quantity
        state.status = SkuStatus.IN_SYNC
        state.last_synced_at = now
        state.last_error = None
        state.consecutive_failures = 0
    elif outcome.action is EventAction.MISSING_IN_SHOP:
        state.shop_quantity = None
        state.status = SkuStatus.MISSING_IN_SHOP
        state.last_error = None
    elif outcome.action is EventAction.BLOCKED:
        state.status = SkuStatus.BLOCKED
        state.last_error = "Blocked by the zero-stock guard"
    else:
        state.status = SkuStatus.FAILED
        state.last_error = outcome.error
        state.consecutive_failures += 1
