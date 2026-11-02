import asyncio
import math
import os
import random
import time
from collections.abc import Callable
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


class ShopProduct(BaseModel):
    sku: str
    title: str
    stock: int
    updated_at: datetime


class StockUpdate(BaseModel):
    quantity: int = Field(ge=0)


class ChaosSettings(BaseModel):
    failure_rate: float = Field(ge=0, le=1)
    latency_ms: int = Field(ge=0, le=10_000)
    rate_limit_requests: int = Field(ge=1, le=1000)


class RateLimiter:
    def __init__(
        self,
        max_requests: int,
        window_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.clock = clock
        self.window_start = clock()
        self.count = 0

    def check(self) -> None:
        now = self.clock()
        if now - self.window_start >= self.window_seconds:
            self.window_start = now
            self.count = 0

        if self.count >= self.max_requests:
            remaining = self.window_start + self.window_seconds - now
            retry_after = max(1, math.ceil(remaining))
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        self.count += 1


rate_limiter = RateLimiter(
    max_requests=int(os.getenv("SHOP_RATE_LIMIT_REQUESTS", "10")),
    window_seconds=float(os.getenv("SHOP_RATE_LIMIT_WINDOW_SECONDS", "10")),
)
failure_rate = float(os.getenv("SHOP_FAILURE_RATE", "0.0"))
latency_ms = int(os.getenv("SHOP_LATENCY_MS", "0"))


def limit_rate() -> None:
    rate_limiter.check()


async def add_latency() -> None:
    if latency_ms > 0:
        await asyncio.sleep(latency_ms / 1000)


def inject_failure() -> None:
    if random.random() < failure_rate:
        raise HTTPException(status_code=503, detail="Shop temporarily unavailable")


router = APIRouter(
    prefix="/shop",
    tags=["Shop"],
    dependencies=[Depends(limit_rate), Depends(add_latency), Depends(inject_failure)],
)
admin_router = APIRouter(prefix="/shop/admin", tags=["Shop Admin"])

products: dict[str, ShopProduct] = {
    sku: ShopProduct(
        sku=sku,
        title=title,
        stock=stock,
        updated_at=datetime.now(UTC),
    )
    for sku, title, stock in [
        ("TSHIRT-BLK-M", "T-Shirt Black M", 38),
        ("TSHIRT-WHT-L", "T-Shirt White L", 22),
        ("HOODIE-GRY-L", "Hoodie Grey L", 15),
        ("HOODIE-BLK-M", "Hoodie Black M", 10),
        ("LEGGINGS-BLU-S", "Leggings Blue S", 3),
        ("LEGGINGS-BLK-M", "Leggings Black M", 31),
        ("SPORTSBRA-PNK-S", "Sports Bra Pink S", 12),
        ("SHORTS-OLV-M", "Shorts Olive M", 18),
        ("JOGGER-GRY-L", "Joggers Grey L", 9),
        ("TANKTOP-WHT-S", "Tank Top White S", 25),
        ("SOCKS-WHT-OS", "Sports Socks White One Size", 120),
        ("CAP-BLK-OS", "Cap Black One Size", 12),
    ]
}


@router.get("/products")
def list_products() -> list[ShopProduct]:
    return list(products.values())


@router.put("/products/{sku}/stock")
def set_stock(sku: str, update: StockUpdate) -> ShopProduct:
    product = products.get(sku)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Unknown SKU: {sku}")

    product.stock = update.quantity
    product.updated_at = datetime.now(UTC)
    return product


@admin_router.get("/chaos")
def get_chaos() -> ChaosSettings:
    return ChaosSettings(
        failure_rate=failure_rate,
        latency_ms=latency_ms,
        rate_limit_requests=rate_limiter.max_requests,
    )


@admin_router.put("/chaos")
def set_chaos(settings: ChaosSettings) -> ChaosSettings:
    global failure_rate, latency_ms
    failure_rate = settings.failure_rate
    latency_ms = settings.latency_ms
    rate_limiter.max_requests = settings.rate_limit_requests
    return settings
