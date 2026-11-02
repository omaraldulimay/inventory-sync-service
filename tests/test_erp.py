from datetime import UTC, datetime, timedelta


def test_list_products(client):
    response = client.get("/erp/products")

    assert response.status_code == 200
    assert len(response.json()) == 12
    assert "JACKET-BLK-L" in {item["sku"] for item in response.json()}


def test_updated_since_returns_only_changed(client):
    before = datetime.now(UTC)
    client.patch("/erp/products/TSHIRT-BLK-M", json={"quantity": 7})

    response = client.get("/erp/products", params={"updated_since": before.isoformat()})

    assert [p["sku"] for p in response.json()] == ["TSHIRT-BLK-M"]
    older = (before - timedelta(hours=1)).isoformat()
    assert len(client.get("/erp/products", params={"updated_since": older}).json()) == 12


def test_updated_since_naive_is_utc(client):
    before = datetime.now(UTC).replace(tzinfo=None)
    client.patch("/erp/products/TSHIRT-BLK-M", json={"quantity": 7})

    response = client.get("/erp/products", params={"updated_since": before.isoformat()})

    assert [p["sku"] for p in response.json()] == ["TSHIRT-BLK-M"]


def test_patch_updates_quantity_and_timestamp(client):
    before = client.get("/erp/products").json()
    old = next(p for p in before if p["sku"] == "TSHIRT-BLK-M")

    response = client.patch("/erp/products/TSHIRT-BLK-M", json={"quantity": 7})

    assert response.status_code == 200
    body = response.json()
    assert body["quantity"] == 7
    assert body["quantity"] != old["quantity"]
    assert body["updated_at"] != old["updated_at"]


def test_patch_unknown_sku(client):
    response = client.patch("/erp/products/NOPE", json={"quantity": 1})

    assert response.status_code == 404
    assert response.json()["detail"] == "Unknown SKU: NOPE"


def test_patch_negative_quantity(client):
    response = client.patch("/erp/products/TSHIRT-BLK-M", json={"quantity": -1})

    assert response.status_code == 422
