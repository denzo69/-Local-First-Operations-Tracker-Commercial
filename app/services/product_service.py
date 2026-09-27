from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import Product, ProductBarcode
from app.services.money_service import parse_decimal


GTIN_LENGTHS = {
    8: "ean8",
    12: "upca",
    13: "ean13",
    14: "gtin14",
}

PRICE_COLUMNS = (
    "unit_price",
    "price",
    "price_eur",
    "selling_price",
    "sales_price",
    "unitprice",
)
VAT_COLUMNS = ("vat_percent", "vat", "alv", "alv_percent")


def normalize_sku(value: str | None) -> str:
    return (value or "").strip().upper()


def next_product_sku(db: Session) -> str:
    next_id = (db.query(Product.id).order_by(Product.id.desc()).limit(1).scalar() or 0) + 1
    while True:
        candidate = f"P-{next_id:06d}"
        if db.query(Product.id).filter(Product.sku == candidate).first() is None:
            return candidate
        next_id += 1


def normalize_barcode(value: str | None) -> str:
    return (value or "").strip()


def gtin_check_digit(value_without_check_digit: str) -> int:
    total = 0
    for position, digit in enumerate(reversed(value_without_check_digit), start=1):
        total += int(digit) * (3 if position % 2 == 1 else 1)
    return (10 - total % 10) % 10


def detect_barcode_symbology(code: str) -> str:
    if code.isdigit() and len(code) in GTIN_LENGTHS:
        return GTIN_LENGTHS[len(code)]
    return "code128"


def validate_barcode(value: str | None) -> tuple[str, str] | tuple[None, None]:
    code = normalize_barcode(value)
    if not code:
        return None, None
    if len(code) > 100:
        raise ValueError("Barcode must be at most 100 characters.")
    symbology = detect_barcode_symbology(code)
    if symbology != "code128":
        expected = gtin_check_digit(code[:-1])
        if expected != int(code[-1]):
            raise ValueError(f"Invalid {symbology.upper()} check digit.")
    return code, symbology


def set_primary_barcode(
    db: Session,
    *,
    product: Product,
    value: str | None,
) -> ProductBarcode | None:
    code, symbology = validate_barcode(value)
    current = next((barcode for barcode in product.barcodes if barcode.is_primary), None)
    if code is None:
        if current is not None:
            db.delete(current)
        return None

    duplicate = db.query(ProductBarcode).filter(ProductBarcode.code == code).first()
    if duplicate is not None and duplicate.product_id != product.id:
        raise ValueError("Barcode is already assigned to another product.")

    if current is None:
        current = ProductBarcode(
            product=product,
            code=code,
            symbology=symbology,
            unit_multiplier=Decimal("1.000"),
            is_primary=True,
        )
        db.add(current)
    else:
        current.code = code
        current.symbology = symbology
    return current


def primary_barcode(product: Product) -> ProductBarcode | None:
    return next((barcode for barcode in product.barcodes if barcode.is_primary), None)


def _first_value(row: dict[str, str], columns: tuple[str, ...], default: str = "") -> str:
    for column in columns:
        value = (row.get(column) or "").strip()
        if value:
            return value
    return default


def upsert_product_from_row(
    db: Session,
    row: dict[str, str],
    *,
    default_vat_percent: str = "24",
) -> Product | None:
    name = (row.get("name") or "").strip()
    if not name:
        return None

    sku = normalize_sku(row.get("sku") or row.get("product_code"))
    barcode_field_present = any(key in row for key in ("barcode", "gtin", "ean"))
    barcode_value = row.get("barcode") or row.get("gtin") or row.get("ean")
    barcode, _ = validate_barcode(barcode_value)
    product_by_sku = db.query(Product).filter(Product.sku == sku).first() if sku else None
    product_by_barcode = (
        db.query(Product)
        .join(ProductBarcode)
        .filter(ProductBarcode.code == barcode)
        .first()
        if barcode
        else None
    )
    if product_by_sku is not None and product_by_barcode is not None and product_by_sku.id != product_by_barcode.id:
        raise ValueError("SKU and barcode refer to different products.")

    product = product_by_sku or product_by_barcode
    if product is None and not sku and not barcode:
        matches = db.query(Product).filter(Product.name == name).all()
        if len(matches) > 1:
            raise ValueError(f"Product name '{name}' is ambiguous; provide sku or barcode.")
        product = matches[0] if matches else None

    if product is None:
        product = Product(sku=sku or next_product_sku(db), name=name)
        db.add(product)
        db.flush()
    elif sku and product.sku != sku:
        product.sku = sku

    product.name = name
    product.description = (row.get("description") or "").strip() or None
    product.unit_price = parse_decimal(_first_value(row, PRICE_COLUMNS, "0"))
    product.vat_percent = parse_decimal(
        _first_value(row, VAT_COLUMNS, default_vat_percent),
        default_vat_percent,
    )
    product.unit = (row.get("unit") or "pcs").strip() or "pcs"
    product.is_active = True
    if barcode_field_present:
        set_primary_barcode(db, product=product, value=barcode)
    return product
