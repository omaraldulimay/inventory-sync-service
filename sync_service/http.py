import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 5
    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 30.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.max_delay_seconds)
        # Full jitter spreads retries out so that clients do not hit the API at the same moment.
        ceiling = min(self.max_delay_seconds, self.base_delay_seconds * 2 ** (attempt - 1))
        return random.uniform(0, ceiling)


class RetriesExhausted(Exception):
    def __init__(self, attempts: int, reason: str) -> None:
        super().__init__(f"Gave up after {attempts} attempts: {reason}")
        self.attempts = attempts
        self.reason = reason


class Throttle:
    # Spaces requests evenly so we stay below the partner's rate limit instead of running into it.
    def __init__(
        self,
        requests_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.interval = 1 / requests_per_second
        self.clock = clock
        self.sleep = sleep
        self._next_slot = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = self.clock()
            wait = self._next_slot - now
            if wait > 0:
                await self.sleep(wait)
            self._next_slot = max(now, self._next_slot) + self.interval


@dataclass(frozen=True)
class CallResult:
    response: httpx.Response
    attempts: int


def parse_retry_after(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


async def send_with_retry(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    policy: RetryPolicy,
    throttle: Throttle | None = None,
    sleep: Sleep = asyncio.sleep,
    **kwargs: Any,
) -> CallResult:
    reason = "no attempt made"
    for attempt in range(1, policy.max_attempts + 1):
        if throttle is not None:
            await throttle.acquire()

        retry_after = None
        try:
            response = await client.request(method, url, **kwargs)
        except httpx.TransportError as error:
            reason = type(error).__name__
        else:
            if response.status_code not in RETRYABLE_STATUS_CODES:
                return CallResult(response, attempt)
            reason = f"HTTP {response.status_code}"
            retry_after = parse_retry_after(response.headers.get("Retry-After"))

        if attempt < policy.max_attempts:
            delay = policy.delay(attempt, retry_after)
            logger.warning(
                "%s %s failed with %s, retry %d/%d in %.1fs",
                method,
                url,
                reason,
                attempt,
                policy.max_attempts - 1,
                delay,
            )
            await sleep(delay)

    raise RetriesExhausted(policy.max_attempts, reason)
