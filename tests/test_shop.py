import pytest
from fastapi import HTTPException

from mock_systems import shop


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def test_list_includes_shop_only_product(client):
    response = client.get("/shop/products")

    assert response.status_code == 200
    assert "CAP-BLK-OS" in {item["sku"] for item in response.json()}


def test_put_sets_absolute_value_idempotently(client):
    first = client.put("/shop/products/TSHIRT-BLK-M/stock", json={"quantity": 40})
    second = client.put("/shop/products/TSHIRT-BLK-M/stock", json={"quantity": 40})

    assert first.status_code == second.status_code == 200
    assert first.json()["stock"] == second.json()["stock"] == 40
    listed = client.get("/shop/products").json()
    assert next(p for p in listed if p["sku"] == "TSHIRT-BLK-M")["stock"] == 40


def test_put_unknown_sku(client):
    response = client.put("/shop/products/NOPE/stock", json={"quantity": 1})

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown SKU: NOPE"


def test_put_negative_quantity(client):
    response = client.put("/shop/products/TSHIRT-BLK-M/stock", json={"quantity": -5})

    assert response.status_code == 422


def test_rate_limiter_window():
    clock = FakeClock()
    limiter = shop.RateLimiter(2, 10, clock)

    limiter.check()
    limiter.check()
    clock.now += 3.5
    with pytest.raises(HTTPException) as exc:
        limiter.check()
    assert exc.value.status_code == 429
    assert exc.value.headers["Retry-After"] == "7"

    clock.now += 6.5
    limiter.check()


def test_rate_limiter_retry_after_minimum_one():
    clock = FakeClock()
    limiter = shop.RateLimiter(1, 10, clock)

    limiter.check()
    clock.now += 9.9
    with pytest.raises(HTTPException) as exc:
        limiter.check()
    assert exc.value.headers["Retry-After"] == "1"


def test_endpoint_rate_limited(client, monkeypatch):
    monkeypatch.setattr(shop, "rate_limiter", shop.RateLimiter(2, 60))

    assert client.get("/shop/products").status_code == 200
    assert client.get("/shop/products").status_code == 200
    response = client.get("/shop/products")

    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1


def test_endpoint_failure_injection(client, monkeypatch):
    monkeypatch.setattr(shop, "failure_rate", 1.0)

    response = client.get("/shop/products")

    assert response.status_code == 503
    assert response.json()["detail"] == "Shop temporarily unavailable"


def test_chaos_defaults(client):
    response = client.get("/shop/admin/chaos")

    assert response.status_code == 200
    assert response.json() == {
        "failure_rate": 0.0,
        "latency_ms": 0,
        "rate_limit_requests": 1000,
    }


def test_chaos_put_changes_behaviour(client):
    body = {"failure_rate": 1.0, "latency_ms": 0, "rate_limit_requests": 50}

    put = client.put("/shop/admin/chaos", json=body)

    assert put.status_code == 200
    assert put.json() == body
    assert client.get("/shop/products").status_code == 503
    assert client.get("/shop/admin/chaos").status_code == 200
    assert shop.rate_limiter.max_requests == 50


def test_chaos_put_validation(client):
    body = {"failure_rate": 1.5, "latency_ms": 0, "rate_limit_requests": 10}

    assert client.put("/shop/admin/chaos", json=body).status_code == 422


def test_admin_not_rate_limited(client, monkeypatch):
    monkeypatch.setattr(shop, "rate_limiter", shop.RateLimiter(1, 60))

    codes = [client.get("/shop/admin/chaos").status_code for _ in range(3)]

    assert codes == [200, 200, 200]


def test_latency_applied(client, monkeypatch):
    delays: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(shop.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(shop, "latency_ms", 50)

    assert client.get("/shop/products").status_code == 200
    assert delays == [0.05]
