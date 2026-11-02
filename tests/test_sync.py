from collections.abc import Callable, Iterator

import httpx
import pytest
from fastapi.testclient import TestClient

from mock_systems import erp, shop
from mock_systems.main import app as mock_app
from sync_service.config import Settings
from sync_service.main import create_app
from sync_service.models import SyncRun
from sync_service.sync import utcnow

DRIFTED_SKUS = {"TSHIRT-BLK-M", "HOODIE-BLK-M", "LEGGINGS-BLU-S", "TANKTOP-WHT-S"}


class RecordingSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class FlakyTransport(httpx.AsyncBaseTransport):
    # Answers the first `failures` requests of one method with an error, then passes through.
    def __init__(
        self, method: str, failures: int, status_code: int, headers: dict[str, str] | None = None
    ) -> None:
        self.inner = httpx.ASGITransport(app=mock_app)
        self.method = method
        self.remaining = failures
        self.status_code = status_code
        self.headers = headers or {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.method == self.method and self.remaining > 0:
            self.remaining -= 1
            return httpx.Response(
                self.status_code, headers=self.headers, json={"detail": "simulated"}
            )
        return await self.inner.handle_async_request(request)


@pytest.fixture
def sleep() -> RecordingSleep:
    return RecordingSleep()


@pytest.fixture
def make_client(tmp_path, sleep) -> Iterator[Callable[..., TestClient]]:
    open_clients: list[TestClient] = []

    def factory(shop_transport: httpx.AsyncBaseTransport | None = None, **overrides) -> TestClient:
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{tmp_path / 'sync.db'}",
            erp_base_url="http://mock",
            shop_base_url="http://mock",
            scheduler_enabled=False,
            shop_requests_per_second=1000,
            **overrides,
        )
        app = create_app(
            settings,
            erp_transport=httpx.ASGITransport(app=mock_app),
            shop_transport=shop_transport or httpx.ASGITransport(app=mock_app),
            sleep=sleep,
        )
        client = TestClient(app)
        client.__enter__()
        open_clients.append(client)
        return client

    yield factory
    for client in open_clients:
        client.__exit__(None, None, None)


def run_sync(client: TestClient, mode: str = "full", dry_run: bool = False) -> dict:
    response = client.post("/api/runs", json={"mode": mode, "dry_run": dry_run})
    assert response.status_code == 201, response.text
    return response.json()


def statuses(client: TestClient) -> dict[str, str]:
    return {item["sku"]: item["status"] for item in client.get("/api/inventory").json()}


def test_full_run_aligns_shop_with_erp(make_client):
    client = make_client()

    run = run_sync(client)

    assert run["status"] == "succeeded"
    assert run["changed"] == 4
    assert {e["sku"] for e in run["events"] if e["action"] == "updated"} == DRIFTED_SKUS
    for sku in DRIFTED_SKUS:
        assert shop.products[sku].stock == erp.products[sku].quantity
    inventory = statuses(client)
    assert inventory["JACKET-BLK-L"] == "missing_in_shop"
    assert inventory["CAP-BLK-OS"] == "unmapped"
    assert list(inventory.values()).count("in_sync") == 11


def test_repeated_run_is_idempotent(make_client):
    client = make_client()
    run_sync(client)

    second = run_sync(client)

    assert second["status"] == "succeeded"
    assert second["changed"] == 0
    assert second["events"] == []


def test_incremental_run_only_syncs_erp_changes(make_client):
    client = make_client()
    run_sync(client)
    assert client.patch("/api/demo/erp/TSHIRT-BLK-M", json={"quantity": 12}).status_code == 200

    run = run_sync(client, mode="incremental")

    assert run["changed"] == 1
    assert run["checked"] < len(erp.products)
    assert shop.products["TSHIRT-BLK-M"].stock == 12


def test_dry_run_plans_without_writing(make_client):
    client = make_client()

    run = run_sync(client, dry_run=True)

    assert run["changed"] == 4
    assert {e["action"] for e in run["events"]} == {"planned"}
    assert shop.products["TSHIRT-BLK-M"].stock == 38
    assert client.get("/api/inventory").json() == []


def test_rate_limited_writes_are_retried_after_retry_after(make_client, sleep):
    transport = FlakyTransport("PUT", failures=2, status_code=429, headers={"Retry-After": "2"})
    client = make_client(shop_transport=transport)

    run = run_sync(client)

    assert run["status"] == "succeeded"
    assert run["changed"] == 4
    assert sum(e["attempts"] for e in run["events"] if e["action"] == "updated") == 6
    assert sleep.calls.count(2.0) == 2
    assert client.get("/api/overview").json()["retries_24h"] == 2
    assert client.get("/api/runs").json()[0]["retries"] == 2


def test_failed_writes_are_retried_by_the_next_incremental_run(make_client):
    transport = FlakyTransport("PUT", failures=1_000, status_code=503)
    client = make_client(shop_transport=transport, retry_max_attempts=3)

    failed = run_sync(client)

    assert failed["status"] == "failed"
    assert failed["failed"] == 4
    inventory = client.get("/api/inventory").json()
    broken = [item for item in inventory if item["status"] == "failed"]
    assert {item["sku"] for item in broken} == DRIFTED_SKUS
    assert all(item["last_error"] == "HTTP 503" for item in broken)

    transport.remaining = 0
    recovered = run_sync(client, mode="incremental")

    assert recovered["status"] == "succeeded"
    assert recovered["changed"] == 4
    assert "failed" not in statuses(client).values()


def test_zero_stock_guard_blocks_suspicious_runs(make_client):
    client = make_client()
    for product in erp.products.values():
        product.quantity = 0

    run = run_sync(client)

    assert run["status"] == "blocked"
    assert "Zero-stock guard" in run["error"]
    assert shop.products["TSHIRT-BLK-M"].stock == 38
    assert "blocked" in statuses(client).values()


def test_blocked_updates_can_be_confirmed_by_a_person(make_client):
    client = make_client()
    for product in erp.products.values():
        product.quantity = 0
    assert run_sync(client)["status"] == "blocked"

    response = client.post("/api/runs", json={"mode": "full", "override_guard": True})

    assert response.json()["status"] == "succeeded"
    assert shop.products["TSHIRT-BLK-M"].stock == 0
    assert "blocked" not in statuses(client).values()


def test_shop_outage_fails_the_run_cleanly(make_client, monkeypatch):
    monkeypatch.setattr(shop, "failure_rate", 1.0)
    client = make_client(retry_max_attempts=2)

    run = run_sync(client)

    assert run["status"] == "failed"
    assert "HTTP 503" in run["error"]


def test_only_one_run_at_a_time_and_recovery_after_restart(make_client):
    client = make_client()

    async def insert_running_run() -> None:
        async with client.app.state.session_factory() as session:
            session.add(
                SyncRun(mode="full", trigger="manual", status="running", started_at=utcnow())
            )
            await session.commit()

    client.portal.call(insert_running_run)

    assert client.post("/api/runs", json={}).status_code == 409

    restarted = make_client()
    assert restarted.get("/api/runs").json()[0]["status"] == "aborted"
    assert run_sync(restarted)["status"] == "succeeded"


def test_overview_summarises_state(make_client):
    client = make_client()
    run_sync(client)

    overview = client.get("/api/overview").json()

    assert overview["total_skus"] == 13
    assert overview["sku_counts"] == {"in_sync": 11, "missing_in_shop": 1, "unmapped": 1}
    assert overview["runs_24h"] == 1
    assert overview["last_run"]["status"] == "succeeded"


def test_scheduler_can_be_paused(make_client):
    client = make_client()

    response = client.put("/api/scheduler", json={"paused": True})

    assert response.json()["paused"] is True


def test_chaos_settings_are_forwarded_to_the_shop(make_client):
    client = make_client()

    response = client.put(
        "/api/demo/chaos", json={"failure_rate": 0.3, "latency_ms": 0, "rate_limit_requests": 10}
    )

    assert response.status_code == 200
    assert shop.failure_rate == 0.3


def test_dashboard_is_served(make_client):
    client = make_client()

    response = client.get("/")

    assert response.status_code == 200
    assert "Inventory Sync" in response.text
