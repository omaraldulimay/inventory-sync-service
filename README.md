# Inventory Sync Service

[![CI](https://github.com/omaraldulimay/inventory-sync-service/actions/workflows/ci.yml/badge.svg)](https://github.com/omaraldulimay/inventory-sync-service/actions/workflows/ci.yml)

A service that keeps stock levels in an online shop in line with the ERP – reliably, even when the shop's API
rate-limits, times out or fails. Built with FastAPI, async SQLAlchemy and PostgreSQL, with a live dashboard.

![Dashboard](docs/screenshots/dashboard.png)

## The problem

In e-commerce the ERP knows the real stock, but customers buy in the shop. If the shop shows stock that no
longer exists, you oversell; if it shows too little, you lose sales. A naive sync ("read ERP, write shop")
breaks in exactly the situations that matter:

- the shop API answers **429 Too Many Requests** or **5xx** during peak traffic,
- a request times out and you don't know whether it was applied,
- two sync runs overlap,
- a broken ERP export suddenly reports **0 for everything** and empties the shop,
- someone changes stock directly in the shop.

This project handles each of these cases explicitly.

## Features

| Feature | Why it matters |
|---|---|
| **Incremental sync + full reconciliation** | Every 5 s only ERP changes since the last cursor are fetched; every 60 s a full comparison catches drift made directly in the shop. |
| **Idempotent writes** | The service sends absolute values ("set to 38"), never deltas ("minus 2"). Retrying after a timeout can't double-count. |
| **Retry with exponential backoff, full jitter and `Retry-After`** | Transient 429/5xx/timeouts are retried; the partner's `Retry-After` header wins over our own backoff. |
| **Client-side throttling** | Requests to the shop are spaced below its rate limit, so we avoid 429s instead of only reacting to them. |
| **Zero-stock guard + human override** | A run that would set most items to 0 is blocked; a person can review and confirm it from the dashboard. |
| **Dry run** | Shows exactly what a run would change without writing anything. |
| **Single active run, enforced by the database** | A partial unique index allows only one `running` sync; interrupted runs are cleaned up on restart. |
| **Failed SKUs are retried automatically** | A SKU that still fails after all retries is marked `failed` and picked up again by the next incremental run. |
| **Audit trail & dashboard** | Every run and every change is stored with attempts, duration and error, and visualised live. |
| **Chaos controls** | Inject failure rate, latency and a tighter rate limit into the mock shop to demonstrate resilience live. |

## Architecture

```mermaid
flowchart LR
    subgraph Mocks [Mock systems · :8000]
        ERP["ERP API<br/>GET /erp/products?updated_since"]
        SHOP["Shop API<br/>PUT /shop/products/{sku}/stock<br/>rate limit · failures · latency"]
    end
    subgraph Sync [Sync service · :8001]
        SCHED[Scheduler] --> ENGINE[Sync engine]
        API[REST API] --> ENGINE
        ENGINE --> HTTP["HTTP layer<br/>retry · backoff · throttle"]
        DASH[Dashboard] --> API
    end
    HTTP --> ERP
    HTTP --> SHOP
    ENGINE --> DB[(PostgreSQL<br/>runs · events · SKU state)]
    API --> DB
```

The mock systems simulate two third-party systems with different field names (`quantity` vs. `stock`) and
realistic problems: SKUs that exist only on one side, rate limiting and random outages.

### How a sync run works

1. **Acquire the run.** Insert a `running` row; the partial unique index rejects a second concurrent run (HTTP 409).
2. **Observe.** *Incremental:* ask the ERP for everything changed since the newest known change (`>=`, so
   boundary items are harmless repeats) plus SKUs whose last write failed. *Full:* read both systems completely.
3. **Plan.** Compare per SKU. Only SKUs whose shop value differs from the ERP need a write.
4. **Guard.** If more than half of the planned writes would set stock to 0, block the run and write nothing.
5. **Write.** Concurrent `PUT`s (bounded by a semaphore), each through throttle → retry → backoff.
6. **Record.** Update the per-SKU state, store an event per change and finish the run as `succeeded`,
   `partial`, `failed` or `blocked`. Scheduled runs that changed nothing are not stored, to keep the history readable.

## Quick start

### With Docker (recommended)

```bash
docker compose up --build
```

Open the dashboard at <http://localhost:8001>. The mock systems run at <http://localhost:8000/docs>, the sync
service API at <http://localhost:8001/docs>. PostgreSQL is used as the database.

### Without Docker

Requires Python 3.12. The sync service then uses SQLite.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

uvicorn mock_systems.main:app --port 8000          # terminal 1
uvicorn sync_service.main:app --port 8001          # terminal 2
```

### A two-minute demo

The dashboard UI is in German; the button names are given as they appear.

1. Click **Pausieren** (top right) to stop the automatic sync.
2. Change a stock value in the **ERP-Bestand ändern** column, then click **Dry-Run** – the activity feed
   (*Aktivität*) shows the planned change, the shop is untouched.
3. Under **Störungen simulieren** set the failure rate to 40 % and click **Sync jetzt**. The run still
   succeeds; the activity feed shows how many attempts each SKU needed.
4. Set several ERP values to 0 and start a **Vollabgleich** – the zero-stock guard blocks it and offers a
   reviewed override (**Prüfen & trotzdem übertragen**).

## Configuration

All settings are environment variables with sensible defaults (see [`.env.example`](.env.example)).

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `sqlite+aiosqlite:///./sync.db` | e.g. `postgresql+asyncpg://user:pass@host/db` |
| `ERP_BASE_URL` / `SHOP_BASE_URL` | `http://127.0.0.1:8000` | Partner APIs |
| `SHOP_REQUESTS_PER_SECOND` | `0.9` | Client-side throttle, below the shop's limit |
| `RETRY_MAX_ATTEMPTS` | `5` | Attempts per request, including the first |
| `RETRY_BASE_DELAY_SECONDS` / `RETRY_MAX_DELAY_SECONDS` | `0.5` / `30` | Exponential backoff with full jitter |
| `MAX_CONCURRENCY` | `4` | Parallel writes per run |
| `INCREMENTAL_INTERVAL_SECONDS` / `FULL_INTERVAL_SECONDS` | `5` / `60` | Scheduler cadence |
| `ZERO_STOCK_GUARD_RATIO` / `ZERO_STOCK_GUARD_MIN_ITEMS` | `0.5` / `3` | When a run is blocked |
| `DEMO_MODE` | `true` | Enables the ERP editor and chaos controls in the dashboard |

## Testing

```bash
pytest -q            # 41 tests
ruff check . && ruff format --check .
```

- **Unit tests** for the retry policy, `Retry-After` parsing, transport errors and the throttle (with a fake clock).
- **End-to-end tests** run the real mock systems in-process via `httpx.ASGITransport` – no network, no sleeping:
  429 with `Retry-After`, exhausted retries and recovery, outage, zero-stock guard and override, dry run,
  idempotency, concurrent-run rejection and crash recovery.
- **CI** runs lint and tests on every push, then starts the full Docker Compose stack with PostgreSQL and runs
  [`scripts/smoke_test.py`](scripts/smoke_test.py) against it.

![Dashboard during a shop outage (dark mode)](docs/screenshots/dashboard-outage-dark.png)

## Project structure

```
mock_systems/      Simulated ERP and shop (rate limit, failures, latency, chaos admin API)
sync_service/
  http.py          Retry policy, backoff with jitter, Retry-After, throttle
  clients.py       ERP and shop clients, mapping both formats to one StockLevel
  sync.py          Sync engine: observe → plan → guard → write → record
  scheduler.py     Background loop for incremental and full runs
  models.py        Runs, events and per-SKU state (SQLAlchemy 2.0, async)
  api.py           REST API and demo endpoints
dashboard/         Dependency-free HTML/CSS/JS dashboard with SVG charts
tests/             Unit and end-to-end tests
scripts/           Smoke test used by CI against the Docker stack
```

## Design decisions

- **Polling with a cursor instead of webhooks.** Many ERPs only offer polling; a cursor keeps it cheap. The full
  reconciliation is the safety net that webhook-only designs often lack.
- **Absolute values instead of deltas.** Makes every write idempotent, which is what makes retries safe.
- **The database as the lock.** A partial unique index is simpler and more robust than an in-process lock and
  keeps working with several instances.
- **Read-only dashboard data from our own database.** The dashboard never calls the partner APIs, so watching
  it costs no rate-limit budget.
- **No frontend framework.** The dashboard is under 800 lines of plain JavaScript; a build step would add more
  complexity than it saves at this size.

## Possible next steps

- Alembic migrations instead of `create_all`
- Webhooks from the ERP to shorten latency further, with the full reconciliation as fallback
- Metrics endpoint (Prometheus) and alerting on blocked or failing runs
- Authentication for the dashboard and demo endpoints
