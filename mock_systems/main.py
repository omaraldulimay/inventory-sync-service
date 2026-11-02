from fastapi import FastAPI

from mock_systems.erp import router as erp_router
from mock_systems.shop import admin_router as shop_admin_router
from mock_systems.shop import router as shop_router

app = FastAPI(title="Mock Systems")
app.include_router(erp_router)
app.include_router(shop_router)
app.include_router(shop_admin_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
