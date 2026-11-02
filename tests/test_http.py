import httpx
import pytest

from sync_service.http import (
    RetriesExhausted,
    RetryPolicy,
    Throttle,
    parse_retry_after,
    send_with_retry,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def client_for(*responses: httpx.Response | Exception) -> httpx.AsyncClient:
    queue = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://test")


def test_delay_prefers_retry_after_but_caps_it():
    policy = RetryPolicy(max_delay_seconds=30)

    assert policy.delay(1, retry_after=5) == 5
    assert policy.delay(1, retry_after=100) == 30


def test_delay_uses_exponential_backoff_with_jitter():
    policy = RetryPolicy(base_delay_seconds=0.5, max_delay_seconds=30)

    delays = [policy.delay(3) for _ in range(50)]

    assert all(0 <= delay <= 2.0 for delay in delays)
    assert len(set(delays)) > 1


def test_parse_retry_after():
    assert parse_retry_after("3") == 3.0
    assert parse_retry_after("-1") == 0.0
    assert parse_retry_after("soon") is None
    assert parse_retry_after(None) is None


@pytest.mark.anyio
async def test_retries_until_success_and_honours_retry_after():
    clock = FakeClock()
    client = client_for(
        httpx.Response(503),
        httpx.Response(429, headers={"Retry-After": "4"}),
        httpx.Response(200, json={"ok": True}),
    )

    result = await send_with_retry(
        client, "GET", "/x", policy=RetryPolicy(max_attempts=5), sleep=clock.sleep
    )

    assert result.response.status_code == 200
    assert result.attempts == 3
    assert len(clock.sleeps) == 2
    assert clock.sleeps[1] == 4


@pytest.mark.anyio
async def test_client_errors_are_not_retried():
    clock = FakeClock()
    client = client_for(httpx.Response(404))

    result = await send_with_retry(client, "GET", "/x", policy=RetryPolicy(), sleep=clock.sleep)

    assert result.response.status_code == 404
    assert result.attempts == 1
    assert clock.sleeps == []


@pytest.mark.anyio
async def test_gives_up_after_max_attempts():
    clock = FakeClock()
    client = client_for(*(httpx.Response(503) for _ in range(3)))

    with pytest.raises(RetriesExhausted) as exc:
        await send_with_retry(
            client, "GET", "/x", policy=RetryPolicy(max_attempts=3), sleep=clock.sleep
        )

    assert exc.value.attempts == 3
    assert exc.value.reason == "HTTP 503"
    assert len(clock.sleeps) == 2


@pytest.mark.anyio
async def test_transport_errors_are_retried():
    clock = FakeClock()
    client = client_for(httpx.ConnectError("refused"), httpx.Response(200))

    result = await send_with_retry(client, "GET", "/x", policy=RetryPolicy(), sleep=clock.sleep)

    assert result.attempts == 2


@pytest.mark.anyio
async def test_throttle_spaces_requests_evenly():
    clock = FakeClock()
    throttle = Throttle(requests_per_second=2, clock=clock, sleep=clock.sleep)

    for _ in range(3):
        await throttle.acquire()

    assert clock.sleeps == [0.5, 0.5]
