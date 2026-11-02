import os
from dataclasses import dataclass


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite+aiosqlite:///./sync.db"
    erp_base_url: str = "http://127.0.0.1:8000"
    shop_base_url: str = "http://127.0.0.1:8000"
    request_timeout_seconds: float = 5.0
    shop_requests_per_second: float = 0.9
    max_concurrency: int = 4
    retry_max_attempts: int = 5
    retry_base_delay_seconds: float = 0.5
    retry_max_delay_seconds: float = 30.0
    scheduler_enabled: bool = True
    incremental_interval_seconds: float = 5.0
    full_interval_seconds: float = 60.0
    zero_stock_guard_ratio: float = 0.5
    zero_stock_guard_min_items: int = 3
    demo_mode: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            database_url=os.getenv("DATABASE_URL", cls.database_url),
            erp_base_url=os.getenv("ERP_BASE_URL", cls.erp_base_url),
            shop_base_url=os.getenv("SHOP_BASE_URL", cls.shop_base_url),
            request_timeout_seconds=float(
                os.getenv("REQUEST_TIMEOUT_SECONDS", cls.request_timeout_seconds)
            ),
            shop_requests_per_second=float(
                os.getenv("SHOP_REQUESTS_PER_SECOND", cls.shop_requests_per_second)
            ),
            max_concurrency=int(os.getenv("MAX_CONCURRENCY", cls.max_concurrency)),
            retry_max_attempts=int(os.getenv("RETRY_MAX_ATTEMPTS", cls.retry_max_attempts)),
            retry_base_delay_seconds=float(
                os.getenv("RETRY_BASE_DELAY_SECONDS", cls.retry_base_delay_seconds)
            ),
            retry_max_delay_seconds=float(
                os.getenv("RETRY_MAX_DELAY_SECONDS", cls.retry_max_delay_seconds)
            ),
            scheduler_enabled=_env_bool("SCHEDULER_ENABLED", cls.scheduler_enabled),
            incremental_interval_seconds=float(
                os.getenv("INCREMENTAL_INTERVAL_SECONDS", cls.incremental_interval_seconds)
            ),
            full_interval_seconds=float(
                os.getenv("FULL_INTERVAL_SECONDS", cls.full_interval_seconds)
            ),
            zero_stock_guard_ratio=float(
                os.getenv("ZERO_STOCK_GUARD_RATIO", cls.zero_stock_guard_ratio)
            ),
            zero_stock_guard_min_items=int(
                os.getenv("ZERO_STOCK_GUARD_MIN_ITEMS", cls.zero_stock_guard_min_items)
            ),
            demo_mode=_env_bool("DEMO_MODE", cls.demo_mode),
        )
