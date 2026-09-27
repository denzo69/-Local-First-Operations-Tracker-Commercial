import csv
from io import StringIO

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.services.product_service import _first_value, upsert_product_from_row
from app.services.settings_service import get_app_settings

router = APIRouter(prefix="/products", tags=["products"])


def _upsert_product(db: Session, row: dict[str, str], *, default_vat_percent: str):
    """Compatibility wrapper for callers of the original import helper."""
    return upsert_product_from_row(db, row, default_vat_percent=default_vat_percent)

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
            try:
                if upsert_product_from_row(
                    db,
                    normalized_row,
                    default_vat_percent=default_vat_percent,
                ) is not None:
                    imported_count += 1
            except ValueError as exc:
                raise ValueError(f"CSV row {row_number}: {exc}") from exc
        db.commit()
    except Exception as exc:
        db.rollback()
        detail = str(exc) if isinstance(exc, ValueError) else f"Invalid product data on CSV row {row_number}"
        raise HTTPException(status_code=400, detail=detail) from exc

    return RedirectResponse(url=f"/products?imported={imported_count}", status_code=303)
