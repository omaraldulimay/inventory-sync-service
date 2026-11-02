import asyncio
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import quote

import httpx

from sync_service.http import RetryPolicy, Sleep, Throttle, send_with_retry


@dataclass(frozen=True)
class StockLevel:
    sku: str
    quantity: int
    name: str | None = None
    updated_at: datetime | None = None


class SkuNotFound(Exception):
    def __init__(self, sku: str, attempts: int) -> None:
        super().__init__(f"Unknown SKU in shop: {sku}")
        self.attempts = attempts


class UnexpectedResponse(Exception):
    pass


def _ensure_ok(response: httpx.Response) -> None:
    if response.is_error:
        raise UnexpectedResponse(f"HTTP {response.status_code} from {response.request.url.path}")


class ErpClient:
    def __init__(
        self, http: httpx.AsyncClient, policy: RetryPolicy, sleep: Sleep = asyncio.sleep
    ) -> None:
        self.http = http
        self.policy = policy
        self.sleep = sleep

    async def fetch_stock(self, updated_since: datetime | None = None) -> list[StockLevel]:
        params = {"updated_since": updated_since.isoformat()} if updated_since else None
        result = await send_with_retry(
            self.http, "GET", "/erp/products", policy=self.policy, sleep=self.sleep, params=params
        )
        _ensure_ok(result.response)
        return [
            StockLevel(
                sku=item["sku"],
                quantity=item["quantity"],
                name=item["name"],
                updated_at=datetime.fromisoformat(item["updated_at"]),
            )
            for item in result.response.json()
        ]


class ShopClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        policy: RetryPolicy,
        throttle: Throttle,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self.http = http
        self.policy = policy
        self.throttle = throttle
        self.sleep = sleep

    async def fetch_stock(self) -> list[StockLevel]:
        result = await send_with_retry(
            self.http,
            "GET",
            "/shop/products",
            policy=self.policy,
            throttle=self.throttle,
            sleep=self.sleep,
        )
        _ensure_ok(result.response)
        return [
            StockLevel(sku=item["sku"], quantity=item["stock"], name=item["title"])
            for item in result.response.json()
        ]

    async def set_stock(self, sku: str, quantity: int) -> int:
        result = await send_with_retry(
            self.http,
            "PUT",
            f"/shop/products/{quote(sku, safe='')}/stock",
            policy=self.policy,
            throttle=self.throttle,
            sleep=self.sleep,
            json={"quantity": quantity},
        )
        if result.response.status_code == 404:
            raise SkuNotFound(sku, result.attempts)
        _ensure_ok(result.response)
        return result.attempts
