import asyncio
import logging
import time
from collections.abc import Callable
from contextlib import suppress

from sync_service.http import Sleep
from sync_service.models import SyncMode
from sync_service.sync import SyncAlreadyRunning, SyncEngine

logger = logging.getLogger(__name__)


class SyncScheduler:
    def __init__(
        self,
        engine: SyncEngine,
        incremental_interval_seconds: float,
        full_interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.engine = engine
        self.incremental_interval_seconds = incremental_interval_seconds
        self.full_interval_seconds = full_interval_seconds
        self.clock = clock
        self.sleep = sleep
        self.paused = False
        self._task: asyncio.Task[None] | None = None

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop(), name="sync-scheduler")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def _loop(self) -> None:
        next_full_at = self.clock()  # reconcile everything right after startup
        while True:
            if not self.paused:
                mode = SyncMode.FULL if self.clock() >= next_full_at else SyncMode.INCREMENTAL
                try:
                    await self.engine.run(mode, trigger="schedule")
                    if mode is SyncMode.FULL:
                        next_full_at = self.clock() + self.full_interval_seconds
                except SyncAlreadyRunning:
                    pass
                except Exception:
                    logger.exception("Scheduled sync could not start")
            await self.sleep(self.incremental_interval_seconds)
