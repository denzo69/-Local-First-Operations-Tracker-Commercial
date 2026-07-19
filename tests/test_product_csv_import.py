from decimal import Decimal

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import Product
from app.services.settings_service import set_app_settings


def test_product_csv_import_accepts_price_eur_and_uses_configured_default_vat():
    with SessionLocal() as db:
        set_app_settings(db, {"default_vat_percent": "25.5"})
        db.commit()

    csv_content = " Name ; Description ; PRICE_EUR ; Unit \nDemo cable;Imported product;12,34;m\n"
    with TestClient(app) as client:
        response = client.post(
            "/products/import",
            files={"csv_file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"] == "/products?imported=1"
    with SessionLocal() as db:
        product = db.query(Product).filter(Product.name == "Demo cable").one()
        assert product.unit_price == Decimal("12.34")
        assert product.vat_percent == Decimal("25.5")
        assert product.unit == "m"


def test_product_csv_import_accepts_common_price_and_vat_aliases_and_updates_by_name():
    with SessionLocal() as db:
        db.add(Product(name="Existing product", unit_price=Decimal("1.00"), vat_percent=Decimal("24"), unit="pcs"))
        db.commit()

    csv_content = "name,selling_price,alv,unit\nExisting product,19.90,14,kpl\n"
    with TestClient(app) as client:
        response = client.post(
            "/products/import",
            files={"csv_file": ("products.csv", csv_content.encode("utf-8"), "text/csv")},
            follow_redirects=False,
        )

    assert response.status_code == 303
    with SessionLocal() as db:
        products = db.query(Product).filter(Product.name == "Existing product").all()
        assert len(products) == 1
        assert products[0].unit_price == Decimal("19.90")
        assert products[0].vat_percent == Decimal("14")
        assert products[0].unit == "kpl"
