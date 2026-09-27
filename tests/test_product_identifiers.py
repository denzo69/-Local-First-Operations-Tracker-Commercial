from decimal import Decimal
from io import BytesIO

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from starlette.datastructures import UploadFile

from app.database import SessionLocal
from app.main import app
from app.models import InventoryBalance, Product, ProductBarcode, WarehouseLocation
from app.services.inventory_service import create_default_warehouse
from app.services.product_service import validate_barcode
from app.routes import products as products_route
from app.services.product_service import next_product_sku, set_primary_barcode, upsert_product_from_row
from app.services.migration_service import _create_unique_index_if_safe


def test_standard_gtin_formats_validate_their_check_digits():
    assert validate_barcode("96385074") == ("96385074", "ean8")
    assert validate_barcode("036000291452") == ("036000291452", "upca")
    assert validate_barcode("4006381333931") == ("4006381333931", "ean13")
    assert validate_barcode("10012345000017") == ("10012345000017", "gtin14")

    try:
        validate_barcode("4006381333932")
    except ValueError as exc:
        assert "check digit" in str(exc)
    else:
        raise AssertionError("Invalid EAN-13 must be rejected")

    assert validate_barcode("INTERNAL-CODE") == ("INTERNAL-CODE", "code128")
    with pytest.raises(ValueError, match="at most 100"):
        validate_barcode("X" * 101)


def test_product_sku_ean_lookup_and_quick_sale_scanner_use_available_stock():
    with TestClient(app) as client:
        created = client.post(
            "/products",
            data={
                "sku": "ean-001",
                "primary_barcode": "4006381333931",
                "name": "EAN product",
                "unit_price": "12.50",
                "vat_percent": "25.5",
                "unit": "pcs",
                "is_stock_item": "on",
            },
            follow_redirects=False,
        )

    assert created.status_code == 303
    with SessionLocal() as db:
        product = db.query(Product).filter(Product.sku == "EAN-001").one()
        barcode = db.query(ProductBarcode).filter(ProductBarcode.product_id == product.id).one()
        barcode_code = barcode.code
        barcode_symbology = barcode.symbology
        create_default_warehouse(db)
        location = db.query(WarehouseLocation).filter(WarehouseLocation.code == "DEFAULT").one()
        db.add(
            InventoryBalance(
                product_id=product.id,
                warehouse_location_id=location.id,
                quantity_on_hand=Decimal("10"),
                quantity_reserved=Decimal("4"),
                quantity_available=Decimal("6"),
            )
        )
        db.commit()

    assert barcode_code == "4006381333931"
    assert barcode_symbology == "ean13"
    with TestClient(app) as client:
        lookup = client.get("/products/barcode-lookup", params={"code": "4006381333931"})
        quick_sale = client.get("/sales/quick")

    assert lookup.status_code == 200
    assert lookup.json()["sku"] == "EAN-001"
    assert lookup.json()["stock_quantity"] == "6.000"
    assert lookup.json()["out_of_stock"] is False
    assert 'id="barcode_scanner"' in quick_sale.text


def test_invalid_or_duplicate_ean_does_not_leave_a_partial_product():
    with TestClient(app) as client:
        invalid = client.post(
            "/products",
            data={"sku": "BAD-EAN", "primary_barcode": "4006381333932", "name": "Bad EAN"},
            follow_redirects=False,
        )
        first = client.post(
            "/products",
            data={"sku": "EAN-ONE", "primary_barcode": "4006381333931", "name": "EAN one"},
            follow_redirects=False,
        )
        duplicate = client.post(
            "/products",
            data={"sku": "EAN-TWO", "primary_barcode": "4006381333931", "name": "EAN two"},
            follow_redirects=False,
        )

    assert invalid.status_code == 400
    assert first.status_code == 303
    assert duplicate.status_code == 400
    with SessionLocal() as db:
        assert db.query(Product).filter(Product.sku.in_(["BAD-EAN", "EAN-TWO"])).count() == 0


def test_csv_identifier_import_is_atomic_and_preserves_omitted_barcode():
    with SessionLocal() as db:
        product = Product(sku="CSV-001", name="CSV old", unit_price=Decimal("1"))
        db.add(product)
        db.flush()
        db.add(
            ProductBarcode(
                product_id=product.id,
                code="4006381333931",
                symbology="ean13",
                is_primary=True,
                unit_multiplier=Decimal("1"),
            )
        )
        db.commit()

    safe_csv = "sku,name,unit_price\nCSV-001,CSV renamed,25.00\n"
    invalid_csv = (
        "sku,barcode,name,unit_price\n"
        "CSV-NEW-1,96385074,CSV first,10.00\n"
        "CSV-NEW-2,4006381333932,CSV invalid,20.00\n"
    )
    with TestClient(app) as client:
        safe = client.post(
            "/products/import",
            files={"csv_file": ("safe.csv", safe_csv.encode(), "text/csv")},
            follow_redirects=False,
        )
        invalid = client.post(
            "/products/import",
            files={"csv_file": ("invalid.csv", invalid_csv.encode(), "text/csv")},
            follow_redirects=False,
        )

    assert safe.status_code == 303
    assert invalid.status_code == 400
    assert "CSV row 3" in invalid.text
    with SessionLocal() as db:
        product = db.query(Product).filter(Product.sku == "CSV-001").one()
        assert product.name == "CSV renamed"
        assert product.barcodes[0].code == "4006381333931"
        assert db.query(Product).filter(Product.sku.in_(["CSV-NEW-1", "CSV-NEW-2"])).count() == 0


def test_identifier_service_collision_update_removal_and_ambiguity_branches():
    with SessionLocal() as db:
        first = Product(sku="P-000003", name="Duplicate name")
        second = Product(sku="SECOND", name="Duplicate name")
        db.add_all([first, second])
        db.flush()
        first_barcode = ProductBarcode(
            product_id=first.id,
            code="4006381333931",
            symbology="ean13",
            is_primary=True,
            unit_multiplier=Decimal("1"),
        )
        second_barcode = ProductBarcode(
            product_id=second.id,
            code="96385074",
            symbology="ean8",
            is_primary=True,
            unit_multiplier=Decimal("1"),
        )
        db.add_all([first_barcode, second_barcode])
        db.commit()

        assert next_product_sku(db) == "P-000004"
        set_primary_barcode(db, product=first, value="036000291452")
        assert first.barcodes[0].code == "036000291452"
        set_primary_barcode(db, product=first, value="")
        db.flush()
        db.expire(first, ["barcodes"])
        assert first.barcodes == []

        with pytest.raises(ValueError, match="ambiguous"):
            upsert_product_from_row(db, {"name": "Duplicate name"})
        with pytest.raises(ValueError, match="different products"):
            upsert_product_from_row(
                db,
                {"sku": "P-000003", "barcode": "96385074", "name": "Conflict"},
            )
        updated = upsert_product_from_row(
            db,
            {"sku": "SECOND-UPDATED", "barcode": "96385074", "name": "Updated by barcode"},
        )
        assert updated is second
        assert updated.sku == "SECOND-UPDATED"


def test_identifier_route_error_and_update_branches():
    with SessionLocal() as db:
        first = Product(sku="ROUTE-ONE", name="Route one")
        second = Product(sku="ROUTE-TWO", name="Route two")
        db.add_all([first, second])
        db.commit()
        first_id = first.id
        second_id = second.id

    with TestClient(app) as client:
        duplicate_create = client.post("/products", data={"sku": "ROUTE-ONE", "name": "Duplicate"})
        invalid_lookup = client.get("/products/barcode-lookup", params={"code": "4006381333932"})
        missing_lookup = client.get("/products/barcode-lookup", params={"code": "4006381333931"})
        duplicate_update = client.post(
            f"/products/{second_id}",
            data={"sku": "ROUTE-ONE", "name": "Route two"},
        )
        invalid_update = client.post(
            f"/products/{first_id}",
            data={"sku": "ROUTE-ONE", "name": "Route one", "primary_barcode": "4006381333932"},
        )

    assert duplicate_create.status_code == 400
    assert invalid_lookup.status_code == 400
    assert missing_lookup.status_code == 404
    assert duplicate_update.status_code == 400
    assert invalid_update.status_code == 400


def test_legacy_products_import_rolls_back_identifier_errors():
    upload = UploadFile(
        filename="products.csv",
        file=BytesIO(
            b"sku,barcode,name\nLEGACY-1,4006381333931,Good\nLEGACY-2,4006381333932,Bad\n"
        ),
    )
    with SessionLocal() as db:
        with pytest.raises(Exception) as exc_info:
            import asyncio

            asyncio.run(products_route.import_products(upload, db=db))
        assert "CSV row 3" in str(exc_info.value)
        assert db.query(Product).filter(Product.sku.in_(["LEGACY-1", "LEGACY-2"])).count() == 0


def test_unique_index_helper_skips_missing_tables_and_columns(tmp_path):
    engine = create_engine(f"sqlite:///{(tmp_path / 'guards.sqlite').as_posix()}", future=True)
    with engine.begin() as connection:
        _create_unique_index_if_safe(connection, table="missing", column="code", index_name="ux_missing")
        connection.exec_driver_sql("CREATE TABLE demo (id INTEGER PRIMARY KEY)")
        _create_unique_index_if_safe(connection, table="demo", column="code", index_name="ux_demo_code")
    engine.dispose()
