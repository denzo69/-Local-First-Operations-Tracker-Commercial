from datetime import date, datetime, time

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import GoodsReceipt, Product, Supplier, User, Warehouse, WarehouseLocation
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
    repair_inventory_caches_from_ledger,
)
from app.services.supplier_service import resolve_goods_receipt_supplier
from app.template_context import templates

router = APIRouter(prefix="/inventory", tags=["inventory"])
settings = get_settings()


def operator_id_from_request(request: Request, fallback_user_id: int | None) -> int:
    current_user = request_current_user(request)
    if current_user is not None:
        return current_user.id
    if fallback_user_id is None:
        raise HTTPException(status_code=400, detail="Operator user is required")
    return fallback_user_id


@router.get("/goods-receipts", response_class=HTMLResponse)
def list_goods_receipts(request: Request, db: Session = Depends(get_db)):
    receipts = db.query(GoodsReceipt).order_by(GoodsReceipt.created_at.desc()).all()
    return templates.TemplateResponse(
        "inventory/goods_receipts/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Goods receipts",
            "receipts": receipts,
        },
    )


@router.get("/goods-receipts/new", response_class=HTMLResponse)
def new_goods_receipt(request: Request, db: Session = Depends(get_db)):
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
        },
    )


@router.post("/goods-receipts")
def create_goods_receipt_route(
    request: Request,
    supplier_id: str = Form(""),
    supplier_name: str = Form(""),
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
        supplier = resolve_goods_receipt_supplier(db, supplier_id=supplier_id, supplier_name=supplier_name)
        receipt = create_goods_receipt(
            db,
            supplier_id=supplier.id,
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
    return RedirectResponse(url=f"/inventory/goods-receipts/{receipt.id}", status_code=303)


@router.get("/goods-receipts/{receipt_id}", response_class=HTMLResponse)
def goods_receipt_detail(receipt_id: int, request: Request, db: Session = Depends(get_db)):
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
        },
    )


@router.post("/goods-receipts/{receipt_id}/lines")
def add_goods_receipt_line_route(
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
    return RedirectResponse(url=f"/inventory/goods-receipts/{receipt_id}", status_code=303)


@router.post("/goods-receipts/{receipt_id}/post")
def post_goods_receipt_route(
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
    return RedirectResponse(url=f"/inventory/goods-receipts/{receipt_id}", status_code=303)


@router.post("/goods-receipts/{receipt_id}/cancel")
def cancel_goods_receipt_route(
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
    return RedirectResponse(url=f"/inventory/goods-receipts/{receipt_id}", status_code=303)


@router.get("/valuation", response_class=HTMLResponse)
def inventory_valuation_report(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "inventory/valuation.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Inventory valuation",
            "valuation": inventory_valuation(db),
        },
    )


@router.get("/reconciliation", response_class=HTMLResponse)
def inventory_reconciliation_report(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "inventory/reconciliation.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Inventory reconciliation",
            "report": inventory_reconciliation(db),
            "users": db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all(),
        },
    )


@router.post("/reconciliation/repair")
def repair_inventory_reconciliation(
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
    return RedirectResponse(url="/inventory/reconciliation", status_code=303)


@router.get("/ledger", response_class=HTMLResponse)
def inventory_ledger_report(
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
    transactions = inventory_ledger(
        db,
        product_id=product_id,
        warehouse_id=warehouse_id,
        supplier_id=supplier_id,
        transaction_type=transaction_type or None,
        user_id=user_id,
        date_from=date_from_at,
        date_to=date_to_at,
    )
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
            "page_title": "Inventory ledger",
            "transactions": transactions,
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
        },
    )


@router.get("/suppliers", response_class=HTMLResponse)
def suppliers(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "inventory/suppliers.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "products",
            "page_title": "Suppliers",
            "suppliers": db.query(Supplier).order_by(Supplier.name.asc()).all(),
        },
    )


@router.post("/suppliers")
def create_supplier(name: str = Form(...), db: Session = Depends(get_db)):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Supplier name is required")
    db.add(Supplier(name=name.strip(), is_active=True))
    db.commit()
    return RedirectResponse(url="/inventory/suppliers", status_code=303)


@router.get("/warehouses", response_class=HTMLResponse)
def warehouses(request: Request, db: Session = Depends(get_db)):
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
        },
    )


@router.post("/warehouses")
def create_warehouse(name: str = Form(...), code: str = Form(...), db: Session = Depends(get_db)):
    if not name.strip() or not code.strip():
        raise HTTPException(status_code=400, detail="Warehouse name and code are required")
    warehouse = Warehouse(name=name.strip(), code=code.strip().upper(), is_active=True)
    db.add(warehouse)
    db.flush()
    db.add(WarehouseLocation(warehouse_id=warehouse.id, code="DEFAULT", name="Default location", is_active=True))
    db.commit()
    return RedirectResponse(url="/inventory/warehouses", status_code=303)
