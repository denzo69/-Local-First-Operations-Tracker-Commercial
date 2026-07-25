import csv
from io import StringIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Product
from app.services.money_service import parse_decimal
from app.services.settings_service import get_app_settings

router = APIRouter(prefix="/products", tags=["products"])

PRICE_COLUMNS = (
    "unit_price",
    "price",
    "price_eur",
    "selling_price",
    "sales_price",
    "unitprice",
)
VAT_COLUMNS = ("vat_percent", "vat", "alv", "alv_percent")


def _first_value(row: dict[str, str], columns: tuple[str, ...], default: str = "") -> str:
    for column in columns:
        value = (row.get(column) or "").strip()
        if value:
            return value
    return default


def _upsert_product(db: Session, row: dict[str, str], *, default_vat_percent: str) -> Product | None:
    name = (row.get("name") or "").strip()
    if not name:
        return None

    product = db.query(Product).filter(Product.name == name).first()
    if product is None:
        product = Product(name=name)
        db.add(product)

    product.description = (row.get("description") or "").strip() or None
    product.unit_price = parse_decimal(_first_value(row, PRICE_COLUMNS, "0"))
    product.vat_percent = parse_decimal(
        _first_value(row, VAT_COLUMNS, default_vat_percent),
        default_vat_percent,
    )
    product.unit = (row.get("unit") or "pcs").strip() or "pcs"
    product.is_active = True
    return product


@router.post("/import")
async def import_products_csv(
    csv_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw = await csv_file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV file must use UTF-8 encoding") from exc

    if not text.strip():
        raise HTTPException(status_code=400, detail="CSV file is empty")

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    except csv.Error as exc:
        raise HTTPException(status_code=400, detail="CSV delimiter could not be detected") from exc

    reader = csv.DictReader(StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header row is required")

    normalized_fieldnames = [(field or "").strip().lower() for field in reader.fieldnames]
    reader.fieldnames = normalized_fieldnames
    if "name" not in normalized_fieldnames:
        raise HTTPException(status_code=400, detail="CSV must include a name column")

    default_vat_percent = get_app_settings(db).get("default_vat_percent", "24") or "24"
    imported_count = 0
    try:
        for row_number, row in enumerate(reader, start=2):
            normalized_row = {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in row.items()
            }
            if _upsert_product(db, normalized_row, default_vat_percent=default_vat_percent) is not None:
                imported_count += 1
        db.commit()
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=f"Invalid product data on CSV row {row_number}") from exc

    return RedirectResponse(url=f"/products?imported={imported_count}", status_code=303)
