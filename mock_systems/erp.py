from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/erp", tags=["ERP"])


class Product(BaseModel):
    sku: str
    name: str
    quantity: int
    updated_at: datetime


class StockUpdate(BaseModel):
    quantity: int = Field(ge=0)


products: dict[str, Product] = {
    sku: Product(
        sku=sku,
        name=name,
        quantity=quantity,
        updated_at=datetime.now(UTC),
    )
    for sku, name, quantity in [
        ("TSHIRT-BLK-M", "T-Shirt Schwarz M", 40),
        ("TSHIRT-WHT-L", "T-Shirt Weiß L", 22),
        ("HOODIE-GRY-L", "Hoodie Grau L", 15),
        ("HOODIE-BLK-M", "Hoodie Schwarz M", 8),
        ("LEGGINGS-BLU-S", "Leggings Blau S", 0),
        ("LEGGINGS-BLK-M", "Leggings Schwarz M", 31),
        ("SPORTSBRA-PNK-S", "Sport-BH Rosa S", 12),
        ("SHORTS-OLV-M", "Shorts Oliv M", 18),
        ("JOGGER-GRY-L", "Jogginghose Grau L", 9),
        ("TANKTOP-WHT-S", "Tanktop Weiß S", 27),
        ("JACKET-BLK-L", "Trainingsjacke Schwarz L", 5),
        ("SOCKS-WHT-OS", "Sportsocken Weiß One Size", 120),
    ]
}


@router.get("/products")
def list_products(updated_since: datetime | None = None) -> list[Product]:
    if updated_since is None:
        return list(products.values())

    if updated_since.tzinfo is None:
        updated_since = updated_since.replace(tzinfo=UTC)
    return [p for p in products.values() if p.updated_at >= updated_since]


@router.patch("/products/{sku}")
def update_stock(sku: str, update: StockUpdate) -> Product:
    product = products.get(sku)
    if product is None:
        raise HTTPException(status_code=404, detail=f"Unknown SKU: {sku}")

    product.quantity = update.quantity
    product.updated_at = datetime.now(UTC)
    return product
