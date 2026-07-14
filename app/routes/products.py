import csv
from datetime import date, datetime, time
from io import StringIO

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import GoodsReceipt, GoodsReceiptLine, InventoryBalance, InventoryTransaction, Product, Supplier, User, Warehouse, WarehouseLocation
from app.services.auth_service import request_current_user
from app.services.inventory_service import (
    add_goods_receipt_line,
    cancel_goods_receipt,
    create_default_warehouse,
    create_goods_receipt,
    inventory_ledger,
    inventory_reconciliation,
    inventory_valuation,
    post_goods_receipt,
    preview_goods_receipt,
    product_cost_profile,
    repair_inventory_caches_from_ledger,
)
from app.services.money_service import parse_decimal
from app.services.settings_service import get_app_settings
from app.template_context import templates

router = APIRouter(prefix="/products", tags=["products"])
settings = get_settings()


def operator_id_from_request(request: Request, fallback_user_id: int | None) -> int:
    current_user = request_current_user(request)
    if current_user is not None:
        return current_user.id
    if fallback_user_id is None:
        raise HTTPException(status_code=400, detail="Operator user is required")
    return fallback_user_id


def products_workspace_summary(db: Session, products: list[Product]) -> dict:
    stock_products = [product for product in products if product.is_stock_item]
    service_products = [product for product in products if not product.is_stock_item]
    active_products = [product for product in products if product.is_active]
    out_of_stock_count = sum(
        1
        for product in stock_products
        if product.is_active and parse_decimal(product.current_inventory_quantity or 0) <= 0
    )
    valuation = inventory_valuation(db)
    return {
        "active_products": len(active_products),
        "stock_products": len(stock_products),
        "service_products": len(service_products),
        "inventory_value": valuation["total_inventory_value_ex_vat"],
        "out_of_stock_count": out_of_stock_count,
    }


def upsert_product_from_row(db: Session, row: dict[str, str]) -> Product | None:
    name = (row.get("name") or "").strip()
    if not name:
        return None

    product = db.query(Product).filter(Product.name == name).first()
    if product is None:
        product = Product(name=name)
        db.add(product)

    product.description = (row.get("description") or "").strip() or None
    product.unit_price = parse_decimal(row.get("unit_price") or row.get("price") or "0")
    product.vat_percent = parse_decimal(row.get("vat_percent") or row.get("vat") or "24", "24")
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
            "summary": products_workspace_summary(db, products),
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
        unit_price=parse_decimal(unit_price),
        vat_percent=parse_decimal(vat_percent, "24"),
        unit=unit.strip() or "pcs",
        is_stock_item=is_stock_item == "on",
    )
    db.add(product)
    db.commit()
    db.refresh(product)
    return RedirectResponse(url="/products", status_code=303)


@router.get("/warehouses", response_class=HTMLResponse)
def product_warehouses(request: Request, db: Session = Depends(get_db)):
    create_default_warehouse(db)
    db.commit()
    return templates.TemplateResponse(
        "inventory/warehouses.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Warehouses",
            "warehouses": db.query(Warehouse).order_by(Warehouse.name.asc()).all(),
            "warehouse_action": "/products/warehouses",
            "products_inventory_base": "/products",
        },
    )


@router.post("/warehouses")
def create_product_warehouse(name: str = Form(...), code: str = Form(...), db: Session = Depends(get_db)):
    if not name.strip() or not code.strip():
        raise HTTPException(status_code=400, detail="Warehouse name and code are required")
    warehouse = Warehouse(name=name.strip(), code=code.strip().upper(), is_active=True)
    db.add(warehouse)
    db.flush()
    db.add(WarehouseLocation(warehouse_id=warehouse.id, code="DEFAULT", name="Default location", is_active=True))
    db.commit()
    return RedirectResponse(url="/products/warehouses", status_code=303)


@router.get("/warehouses/{warehouse_id}", response_class=HTMLResponse)
@router.get("/warehouses/{warehouse_id}/locations", response_class=HTMLResponse)
def product_warehouse_detail(warehouse_id: int, request: Request, db: Session = Depends(get_db)):
    warehouse = db.get(Warehouse, warehouse_id)
    if warehouse is None:
        raise HTTPException(status_code=404, detail="Warehouse not found")
    balances = (
        db.query(InventoryBalance)
        .join(WarehouseLocation, InventoryBalance.warehouse_location_id == WarehouseLocation.id)
        .filter(WarehouseLocation.warehouse_id == warehouse.id)
        .order_by(WarehouseLocation.code.asc())
        .all()
    )
    receipts = (
        db.query(GoodsReceipt)
        .join(GoodsReceiptLine, GoodsReceiptLine.goods_receipt_id == GoodsReceipt.id)
        .join(WarehouseLocation, GoodsReceiptLine.destination_location_id == WarehouseLocation.id)
        .filter(WarehouseLocation.warehouse_id == warehouse.id)
        .order_by(GoodsReceipt.created_at.desc())
        .limit(10)
        .all()
    )
    return templates.TemplateResponse(
        "inventory/warehouse_detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": warehouse.name,
            "warehouse": warehouse,
            "balances": balances,
            "receipts": receipts,
            "total_quantity": sum((parse_decimal(balance.quantity_on_hand or 0) for balance in balances), parse_decimal("0")),
            "total_value": sum((parse_decimal(balance.inventory_value_ex_vat or 0) for balance in balances), parse_decimal("0")),
        },
    )


@router.get("/suppliers", response_class=HTMLResponse)
def product_suppliers(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "inventory/suppliers.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Suppliers",
            "suppliers": db.query(Supplier).order_by(Supplier.name.asc()).all(),
            "supplier_action": "/products/suppliers",
            "products_inventory_base": "/products",
        },
    )


@router.post("/suppliers")
def create_product_supplier(name: str = Form(...), db: Session = Depends(get_db)):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Supplier name is required")
    db.add(Supplier(name=name.strip(), is_active=True))
    db.commit()
    return RedirectResponse(url="/products/suppliers", status_code=303)


@router.get("/goods-receipts", response_class=HTMLResponse)
def product_goods_receipts(request: Request, db: Session = Depends(get_db)):
    receipts = db.query(GoodsReceipt).order_by(GoodsReceipt.created_at.desc()).all()
    return templates.TemplateResponse(
        "inventory/goods_receipts/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Goods receipts",
            "receipts": receipts,
            "products_inventory_base": "/products",
        },
    )


@router.get("/goods-receipts/new", response_class=HTMLResponse)
def new_product_goods_receipt(request: Request, db: Session = Depends(get_db)):
    create_default_warehouse(db)
    db.commit()
    return templates.TemplateResponse(
        "inventory/goods_receipts/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "New goods receipt",
            "suppliers": db.query(Supplier).filter(Supplier.is_active.is_(True)).order_by(Supplier.name.asc()).all(),
            "users": db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all(),
            "today": date.today(),
            "products_inventory_base": "/products",
        },
    )


@router.post("/goods-receipts")
def create_product_goods_receipt_route(
    request: Request,
    supplier_id: int = Form(...),
    receipt_date: date = Form(...),
    delivery_number: str = Form(""),
    invoice_number: str = Form(""),
    freight_total_ex_vat: str = Form("0"),
    freight_vat_rate: str = Form("0"),
    other_costs_total_ex_vat: str = Form("0"),
    other_costs_vat_rate: str = Form("0"),
    allocation_method: str = Form("by_value"),
    received_by_user_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        receipt = create_goods_receipt(
            db,
            supplier_id=supplier_id,
            receipt_date=receipt_date,
            received_by_user_id=operator_id_from_request(request, received_by_user_id),
            delivery_number=delivery_number,
            invoice_number=invoice_number,
            freight_total_ex_vat=freight_total_ex_vat,
            freight_vat_rate=freight_vat_rate,
            other_costs_total_ex_vat=other_costs_total_ex_vat,
            other_costs_vat_rate=other_costs_vat_rate,
            allocation_method=allocation_method,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/products/goods-receipts/{receipt.id}", status_code=303)


@router.get("/goods-receipts/{receipt_id}", response_class=HTMLResponse)
def product_goods_receipt_detail(receipt_id: int, request: Request, db: Session = Depends(get_db)):
    receipt = db.get(GoodsReceipt, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=404, detail="Goods receipt not found")
    create_default_warehouse(db)
    db.commit()
    preview = None
    if receipt.lines:
        try:
            preview = preview_goods_receipt(db, receipt)
        except ValueError:
            preview = None
    return templates.TemplateResponse(
        "inventory/goods_receipts/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Goods receipt",
            "receipt": receipt,
            "preview": preview,
            "products": db.query(Product).filter(Product.is_active.is_(True), Product.is_stock_item.is_(True)).order_by(Product.name.asc()).all(),
            "locations": db.query(WarehouseLocation).filter(WarehouseLocation.is_active.is_(True)).order_by(WarehouseLocation.code.asc()).all(),
            "users": db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all(),
            "products_inventory_base": "/products",
            "reconciliation_ledger_url": "/products/inventory/transactions",
            "reconciliation_repair_action": "/products/inventory/reconciliation/repair",
        },
    )


@router.post("/goods-receipts/{receipt_id}/lines")
def add_product_goods_receipt_line_route(
    receipt_id: int,
    product_id: int = Form(...),
    destination_location_id: int = Form(...),
    quantity_value: str = Form(...),
    purchase_unit_price_ex_vat: str = Form(...),
    vat_rate: str = Form("24"),
    db: Session = Depends(get_db),
):
    try:
        add_goods_receipt_line(
            db,
            goods_receipt_id=receipt_id,
            product_id=product_id,
            destination_location_id=destination_location_id,
            quantity_value=quantity_value,
            purchase_unit_price_ex_vat=purchase_unit_price_ex_vat,
            vat_rate=vat_rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/products/goods-receipts/{receipt_id}", status_code=303)


@router.post("/goods-receipts/{receipt_id}/post")
def post_product_goods_receipt_route(
    receipt_id: int,
    request: Request,
    posted_by_user_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        post_goods_receipt(
            db,
            goods_receipt_id=receipt_id,
            posted_by_user_id=operator_id_from_request(request, posted_by_user_id),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/products/goods-receipts/{receipt_id}", status_code=303)


@router.post("/goods-receipts/{receipt_id}/cancel")
def cancel_product_goods_receipt_route(
    receipt_id: int,
    request: Request,
    reason: str = Form(...),
    user_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        cancel_goods_receipt(
            db,
            goods_receipt_id=receipt_id,
            user_id=operator_id_from_request(request, user_id),
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/products/goods-receipts/{receipt_id}", status_code=303)


@router.get("/inventory", response_class=HTMLResponse)
def product_inventory_balances(
    request: Request,
    product_id: int | None = None,
    warehouse_id: int | None = None,
    db: Session = Depends(get_db),
):
    balance_query = db.query(InventoryBalance).join(Product).join(WarehouseLocation)
    if product_id:
        balance_query = balance_query.filter(InventoryBalance.product_id == product_id)
    if warehouse_id:
        balance_query = balance_query.filter(WarehouseLocation.warehouse_id == warehouse_id)
    balances = balance_query.order_by(Product.name.asc(), WarehouseLocation.code.asc()).all()
    return templates.TemplateResponse(
        "inventory/balances.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Inventory",
            "balances": balances,
            "warehouses": db.query(Warehouse).order_by(Warehouse.name.asc()).all(),
            "products": db.query(Product).order_by(Product.name.asc()).all(),
        },
    )


@router.get("/inventory/transactions", response_class=HTMLResponse)
def product_inventory_transactions(
    request: Request,
    product_id: int | None = None,
    warehouse_id: int | None = None,
    supplier_id: int | None = None,
    transaction_type: str | None = None,
    user_id: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    db: Session = Depends(get_db),
):
    date_from_at = datetime.combine(date_from, time.min) if date_from else None
    date_to_at = datetime.combine(date_to, time.max) if date_to else None
    transaction_types = [
        "purchase",
        "sale",
        "inventory_adjustment",
        "inventory_count",
        "warehouse_transfer",
        "shelf_transfer",
        "customer_return",
        "supplier_return",
        "initial_balance",
        "production_consumption",
        "production_output",
    ]
    return templates.TemplateResponse(
        "inventory/ledger.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Inventory transactions",
            "transactions": inventory_ledger(
                db,
                product_id=product_id,
                warehouse_id=warehouse_id,
                supplier_id=supplier_id,
                transaction_type=transaction_type or None,
                user_id=user_id,
                date_from=date_from_at,
                date_to=date_to_at,
            ),
            "products": db.query(Product).order_by(Product.name.asc()).all(),
            "warehouses": db.query(Warehouse).order_by(Warehouse.name.asc()).all(),
            "suppliers": db.query(Supplier).order_by(Supplier.name.asc()).all(),
            "users": db.query(User).order_by(User.name.asc()).all(),
            "transaction_types": transaction_types,
            "selected": {
                "product_id": product_id,
                "warehouse_id": warehouse_id,
                "supplier_id": supplier_id,
                "transaction_type": transaction_type or "",
                "user_id": user_id,
                "date_from": date_from,
                "date_to": date_to,
            },
            "ledger_action": "/products/inventory/transactions",
            "valuation_url": "/products/inventory/valuation",
        },
    )


@router.get("/inventory/valuation", response_class=HTMLResponse)
def product_inventory_valuation(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "inventory/valuation.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Inventory valuation",
            "valuation": inventory_valuation(db),
            "products_inventory_base": "/products",
        },
    )


@router.get("/inventory/reconciliation", response_class=HTMLResponse)
def product_inventory_reconciliation(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "inventory/reconciliation.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Inventory reconciliation",
            "report": inventory_reconciliation(db),
            "users": db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all(),
            "products_inventory_base": "/products",
        },
    )


@router.post("/inventory/reconciliation/repair")
def repair_product_inventory_reconciliation(
    request: Request,
    reason: str = Form(...),
    user_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        repair_inventory_caches_from_ledger(
            db,
            user_id=operator_id_from_request(request, user_id),
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url="/products/inventory/reconciliation", status_code=303)


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


@router.get("/{product_id}", response_class=HTMLResponse)
def product_detail(product_id: int, request: Request, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    try:
        cost_profile = product_cost_profile(db, product_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return templates.TemplateResponse(
        "products/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "product": product,
            "cost_profile": cost_profile,
            "balances": (
                db.query(InventoryBalance)
                .filter(InventoryBalance.product_id == product.id)
                .join(WarehouseLocation)
                .order_by(WarehouseLocation.code.asc())
                .all()
            ),
            "recent_receipt_lines": (
                db.query(GoodsReceiptLine)
                .filter(GoodsReceiptLine.product_id == product.id)
                .join(GoodsReceipt)
                .order_by(GoodsReceipt.created_at.desc())
                .limit(10)
                .all()
            ),
            "transactions": (
                db.query(InventoryTransaction)
                .filter(InventoryTransaction.product_id == product.id)
                .order_by(InventoryTransaction.created_at.desc())
                .limit(25)
                .all()
            ),
            "page_title": product.name,
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
    product.unit_price = parse_decimal(unit_price)
    product.vat_percent = parse_decimal(vat_percent, "24")
    product.unit = unit.strip() or "pcs"
    product.is_active = is_active == "on"
    product.is_stock_item = is_stock_item == "on"
    db.commit()
    return RedirectResponse(url="/products", status_code=303)
