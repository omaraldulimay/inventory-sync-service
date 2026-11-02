"""End-to-end smoke test against a running docker compose stack.

The sync service is expected at http://localhost:8001 (override with SYNC_URL).
"""

import os
import sys
import time

import httpx

BASE_URL = os.environ.get("SYNC_URL", "http://localhost:8001")
ZERO_SKUS = ["TSHIRT-BLK-M", "TSHIRT-WHT-L", "LEGGINGS-BLK-M", "SHORTS-OLV-M"]


def check(condition: bool, message: str) -> None:
    if not condition:
        sys.exit(f"FAIL: {message}")


def ok(message: str) -> None:
    print(f"✓ {message}")


def wait_for_initial_sync(client: httpx.Client, timeout: float = 90) -> None:
    deadline = time.monotonic() + timeout
    last = "no response"
    while time.monotonic() < deadline:
        try:
            response = client.get("/api/runs")
            response.raise_for_status()
            runs = response.json()
            if any(run["status"] == "succeeded" for run in runs):
                return
            last = f"runs so far: {[run['status'] for run in runs]}"
        except httpx.HTTPError as exc:
            last = repr(exc)
        time.sleep(1)
    sys.exit(f"FAIL: no succeeded run within {timeout:.0f}s ({last})")


def run(client: httpx.Client, **body) -> dict:
    deadline = time.monotonic() + 60
    while True:
        response = client.post("/api/runs", json=body, timeout=120)
        if response.status_code == 409 and time.monotonic() < deadline:
            time.sleep(1)
            continue
        check(
            response.status_code == 201,
            f"POST /api/runs {body} -> {response.status_code} {response.text}",
        )
        return response.json()


def put(client: httpx.Client, path: str, body: dict) -> None:
    response = client.put(path, json=body)
    check(response.is_success, f"PUT {path} {body} -> {response.status_code} {response.text}")


def set_erp(client: httpx.Client, sku: str, quantity: int) -> None:
    response = client.patch(f"/api/demo/erp/{sku}", json={"quantity": quantity})
    check(
        response.is_success,
        f"PATCH ERP {sku}={quantity} -> {response.status_code} {response.text}",
    )


def main() -> None:
    with httpx.Client(base_url=BASE_URL, timeout=30) as client:
        wait_for_initial_sync(client)
        ok("initial full sync")

        overview = client.get("/api/overview").json()
        expected = {"in_sync": 11, "missing_in_shop": 1, "unmapped": 1}
        check(
            overview["sku_counts"] == expected,
            f"sku_counts {overview['sku_counts']} != {expected}",
        )
        ok("overview sku counts")

        put(client, "/api/scheduler", {"paused": True})
        # Drain: a scheduled run may have started just before the pause took effect.
        run(client, mode="incremental")
        ok("scheduler paused")

        set_erp(client, "TSHIRT-BLK-M", 7)
        put(
            client,
            "/api/demo/chaos",
            {"failure_rate": 0.3, "latency_ms": 0, "rate_limit_requests": 10},
        )
        result = run(client, mode="incremental")
        put(
            client,
            "/api/demo/chaos",
            {"failure_rate": 0, "latency_ms": 0, "rate_limit_requests": 10},
        )
        check(result["status"] == "succeeded", f"retry run status {result['status']}: {result}")
        check(result["changed"] == 1, f"retry run changed {result['changed']} != 1")
        ok("retry path under chaos")

        for sku in ZERO_SKUS:
            set_erp(client, sku, 0)
        result = run(client, mode="full")
        check(result["status"] == "blocked", f"guard run status {result['status']}: {result}")
        result = run(client, mode="full", override_guard=True)
        check(result["status"] == "succeeded", f"override run status {result['status']}: {result}")
        ok("zero-stock guard and override")

        set_erp(client, "HOODIE-GRY-L", 3)
        result = run(client, mode="full", dry_run=True)
        check(result["changed"] == 1, f"dry run changed {result['changed']} != 1")
        actions = [event["action"] for event in result["events"]]
        check(all(a == "planned" for a in actions), f"dry run actions {actions}")
        inventory = {item["sku"]: item for item in client.get("/api/inventory").json()}
        shop_quantity = inventory["HOODIE-GRY-L"]["shop_quantity"]
        check(shop_quantity == 15, f"HOODIE-GRY-L shop_quantity {shop_quantity} != 15")
        ok("dry run leaves shop untouched")

    print("All smoke tests passed.")


if __name__ == "__main__":
    main()
