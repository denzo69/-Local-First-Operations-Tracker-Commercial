from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Job, Product, Sale, Shift
from app.services.sales_service import (
    PAYMENT_METHODS,
    add_refund,
    create_sale_with_payment,
    remaining_refundable_amount,
)
from app.template_context import templates

router = APIRouter(prefix="/sales", tags=["sales"])
settings = get_settings()


@router.get("", response_class=HTMLResponse)
def list_sales(request: Request, db: Session = Depends(get_db)):
    sales = db.query(Sale).order_by(Sale.sold_at.desc()).all()
    return templates.TemplateResponse(
        "sales/list.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "sales": sales,
        },
    )


@router.get("/new", response_class=HTMLResponse)
def new_sale(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "sales/form.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "shifts": db.query(Shift).filter(Shift.status == "open").order_by(Shift.opened_at.desc()).all(),
            "products": db.query(Product).filter(Product.is_active.is_(True)).order_by(Product.name.asc()).all(),
            "work_orders": db.query(Job).order_by(Job.created_at.desc()).limit(100).all(),
            "payment_methods": PAYMENT_METHODS,
        },
    )


@router.post("")
def create_sale(
    shift_id: int = Form(...),
    seller_id: int = Form(...),
    payment_method: str = Form(...),
    description: str = Form(...),
    quantity: str = Form("1"),
    unit_price: str = Form("0"),
    vat_percent: str = Form("24"),
    discount_amount: str = Form("0"),
    work_order_id: int | None = Form(None),
    product_id: int | None = Form(None),
    db: Session = Depends(get_db),
):
    try:
        sale = create_sale_with_payment(
            db,
            seller_id=seller_id,
            shift_id=shift_id,
            payment_method=payment_method,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            vat_percent=vat_percent,
            discount_amount=discount_amount,
            work_order_id=work_order_id,
            product_id=product_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale.id}", status_code=303)


@router.get("/{sale_id}", response_class=HTMLResponse)
def sale_detail(sale_id: int, request: Request, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")
    return templates.TemplateResponse(
        "sales/detail.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "sale": sale,
            "open_shifts": db.query(Shift).filter(Shift.status == "open").order_by(Shift.opened_at.desc()).all(),
            "payment_methods": PAYMENT_METHODS,
            "remaining_refundable": remaining_refundable_amount(sale),
        },
    )


@router.post("/{sale_id}/refunds")
def create_refund(
    sale_id: int,
    refund_shift_id: int = Form(...),
    amount: str = Form(...),
    payment_method: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    refund_shift = db.get(Shift, refund_shift_id)
    if refund_shift is None:
        raise HTTPException(status_code=400, detail="Refund shift not found")
    try:
        add_refund(
            db,
            sale_id=sale_id,
            refund_shift_id=refund_shift_id,
            seller_id=refund_shift.seller_id,
            amount=amount,
            payment_method=payment_method,
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale_id}", status_code=303)
