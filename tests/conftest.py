import copy

import pytest
from fastapi.testclient import TestClient

from mock_systems import erp, shop
from mock_systems.main import app


@pytest.fixture(autouse=True)
def isolated_state(monkeypatch):
    erp_backup = copy.deepcopy(erp.products)
    shop_backup = copy.deepcopy(shop.products)
    monkeypatch.setattr(shop, "rate_limiter", shop.RateLimiter(1000, 10))
    monkeypatch.setattr(shop, "failure_rate", 0.0)
    monkeypatch.setattr(shop, "latency_ms", 0)
    yield
    erp.products.clear()
    erp.products.update(erp_backup)
    shop.products.clear()
    shop.products.update(shop_backup)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)
