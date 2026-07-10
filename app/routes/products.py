import csv
from io import StringIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Product
from app.services.settings_service import get_app_settings
from app.template_context import templates

router = APIRouter(prefix="/products", tags=["products"])
settings = get_settings()


def parse_float(value: str, default: float = 0.0) -> float:
    if value is None or not str(value).strip():
        return default
    return float(str(value).replace(",", "."))


def upsert_product_from_row(db: Session, row: dict[str, str]) -> Product | None:
    name = (row.get("name") or "").strip()
    if not name:
        return None

    product = db.query(Product).filter(Product.name == name).first()
    if product is None:
        product = Product(name=name)
        db.add(product)

    product.description = (row.get("description") or "").strip() or None
    product.unit_price = parse_float(row.get("unit_price") or row.get("price") or "0")
    product.vat_percent = parse_float(row.get("vat_percent") or row.get("vat") or "24", 24.0)
    product.unit = (row.get("unit") or "pcs").strip() or "pcs"
    product.is_active = True
    return product


@router.get("", response_class=HTMLResponse)
def list_products(request: Request, db: Session = Depends(get_db)):
    products = db.query(Product).order_by(Product.name.asc()).all()
    return templates.TemplateResponse(
        "products/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "products": products,
        },
    )


@router.post("/import")
async def import_products(
    csv_file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw = await csv_file.read()
    text = raw.decode("utf-8-sig")
    sample = text[:2048]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
    reader = csv.DictReader(StringIO(text), dialect=dialect)

    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV header row is required")

    normalized_fieldnames = [field.strip().lower() for field in reader.fieldnames]
    reader.fieldnames = normalized_fieldnames
    if "name" not in normalized_fieldnames:
        raise HTTPException(status_code=400, detail="CSV must include a name column")

    imported_count = 0
    for row in reader:
        normalized_row = {
            (key or "").strip().lower(): (value or "").strip()
            for key, value in row.items()
        }
        if upsert_product_from_row(db, normalized_row) is not None:
            imported_count += 1

    db.commit()
    return RedirectResponse(url=f"/products?imported={imported_count}", status_code=303)


@router.get("/new", response_class=HTMLResponse)
def new_product(request: Request, db: Session = Depends(get_db)):
    app_settings = get_app_settings(db)
    return templates.TemplateResponse(
        "products/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "product": None,
            "form_action": "/products",
            "page_title": "New product",
            "default_vat_percent": app_settings["default_vat_percent"],
        },
    )


@router.post("")
def create_product(
    name: str = Form(...),
    description: str = Form(""),
    unit_price: str = Form("0"),
    vat_percent: str = Form("24"),
    unit: str = Form("pcs"),
    is_stock_item: str | None = Form(None),
    db: Session = Depends(get_db),
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Product name is required")

    product = Product(
        name=name.strip(),
        description=description.strip() or None,
        unit_price=parse_float(unit_price),
        vat_percent=parse_float(vat_percent, 24.0),
        unit=unit.strip() or "pcs",
        is_stock_item=is_stock_item == "on",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return RedirectResponse(url="/products", status_code=303)


@router.get("/{product_id}/edit", response_class=HTMLResponse)
def edit_product(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")

    return templates.TemplateResponse(
        "products/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "product": product,
            "form_action": f"/products/{product.id}",
            "page_title": "Edit product",
            "default_vat_percent": product.vat_percent,
        },
    )


@router.post("/{product_id}")
def update_product(
    product_id: int,
    name: str = Form(...),
    description: str = Form(""),
    unit_price: str = Form("0"),
    vat_percent: str = Form("24"),
    unit: str = Form("pcs"),
    is_active: str | None = Form(None),
    is_stock_item: str | None = Form(None),
    db: Session = Depends(get_db),
):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    if not name.strip():
        raise HTTPException(status_code=400, detail="Product name is required")

    product.name = name.strip()
    product.description = description.strip() or None
    product.unit_price = parse_float(unit_price)
    product.vat_percent = parse_float(vat_percent, 24.0)
    product.unit = unit.strip() or "pcs"
    product.is_active = is_active == "on"
    product.is_stock_item = is_stock_item == "on"
    db.commit()
    return RedirectResponse(url="/products", status_code=303)
