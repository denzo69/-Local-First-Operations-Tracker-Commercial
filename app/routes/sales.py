from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Job, Product, Role, Sale, Shift, User
from app.services.auth_service import request_current_user
from app.services.sales_service import (
    AuthorizationError,
    PAYMENT_METHODS,
    add_refund,
    correct_sale_seller,
    create_sale_with_payment,
    remaining_refundable_amount,
    user_can_override_sale_seller,
)
from app.services.settings_service import get_app_settings
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
    app_settings = get_app_settings(db)
    active_sellers = (
        db.query(User)
        .join(Role)
        .filter(
            User.is_active.is_(True),
            User.can_receive_sales_credit.is_(True),
            Role.code.in_(["admin", "manager", "seller"]),
        )
        .order_by(User.name.asc())
        .all()
    )
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
            "active_sellers": active_sellers,
            "seller_selection_mode": app_settings.get("sale_seller_selection_mode", "shift_owner"),
        },
    )


@router.post("")
def create_sale(
    request: Request,
    shift_id: int = Form(...),
    seller_id: int | None = Form(None),
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
    current_user = request_current_user(request)
    app_settings = get_app_settings(db)
    selected_seller_id = seller_id
    if selected_seller_id is None:
        shift = db.get(Shift, shift_id)
        selected_seller_id = shift.seller_id if shift is not None else None
    if selected_seller_id is None:
        raise HTTPException(status_code=400, detail="Seller is required")
    try:
        sale = create_sale_with_payment(
            db,
            seller_id=selected_seller_id,
            shift_id=shift_id,
            payment_method=payment_method,
            description=description,
            quantity=quantity,
            unit_price=unit_price,
            vat_percent=vat_percent,
            discount_amount=discount_amount,
            work_order_id=work_order_id,
            product_id=product_id,
            created_by_user_id=current_user.id if current_user is not None else None,
            seller_selection_mode=app_settings.get("sale_seller_selection_mode", "shift_owner"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale.id}", status_code=303)


@router.get("/{sale_id}", response_class=HTMLResponse)
def sale_detail(sale_id: int, request: Request, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")
    active_sellers = (
        db.query(User)
        .join(Role)
        .filter(
            User.is_active.is_(True),
            User.can_receive_sales_credit.is_(True),
            Role.code.in_(["admin", "manager", "seller"]),
        )
        .order_by(User.name.asc())
        .all()
    )
    correction_users = (
        db.query(User)
        .join(Role)
        .filter(User.is_active.is_(True), Role.code.in_(["admin", "manager"]))
        .order_by(User.name.asc())
        .all()
    )
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
            "active_sellers": active_sellers,
            "correction_users": correction_users,
            "can_correct_seller": user_can_override_sale_seller(request_current_user(request)),
        },
    )


@router.get("/{sale_id}/receipt", response_class=HTMLResponse)
def sale_receipt(sale_id: int, request: Request, db: Session = Depends(get_db)):
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")
    return templates.TemplateResponse(
        "sales/receipt.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "sale": sale,
        },
    )


@router.post("/{sale_id}/seller")
def update_sale_seller(
    sale_id: int,
    request: Request,
    sold_by_user_id: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = request_current_user(request)
    if not user_can_override_sale_seller(current_user):
        raise HTTPException(status_code=403, detail="Only Admin or Manager can correct sale seller attribution.")
    try:
        correct_sale_seller(
            db,
            sale_id=sale_id,
            new_sold_by_user_id=sold_by_user_id,
            corrected_by_user_id=current_user.id,
            reason=reason,
        )
    except AuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale_id}", status_code=303)


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
