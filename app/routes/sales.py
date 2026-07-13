from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import Job, Product, Sale, Shift, User
from app.services.auth_service import request_current_user
from app.services.sales_service import (
    AuthorizationError,
    INVOICE_ACTIVE_STATUSES,
    PAYMENT_METHODS,
    PaymentInput,
    SaleLineInput,
    add_refund,
    confirm_invoice_paid,
    confirm_invoice_unpaid,
    correct_sale_seller,
    create_sale_from_lines,
    create_sale_from_work_order,
    create_sale_with_payment,
    invoice_follow_up_status,
    record_invoice_reminder_sent,
    remaining_refundable_amount,
    sale_balance_due,
    sale_paid_amount,
    transfer_sale_to_invoicing,
    user_can_override_sale_seller,
)
from app.template_context import templates

router = APIRouter(prefix="/sales", tags=["sales"])
settings = get_settings()


def _eligible_sellers(db: Session) -> list[User]:
    return (
        db.query(User)
        .join(User.role)
        .filter(
            User.is_active.is_(True),
            or_(User.can_receive_sales_credit.is_(True), User.role.has(code="seller")),
        )
        .order_by(User.name.asc())
        .all()
    )


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
            "sellers": db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all(),
            "payment_methods": PAYMENT_METHODS,
        },
    )


@router.get("/quick", response_class=HTMLResponse)
def quick_sale(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse(
        "sales/quick.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "shifts": db.query(Shift).filter(Shift.status == "open").order_by(Shift.opened_at.desc()).all(),
            "products": db.query(Product).filter(Product.is_active.is_(True)).order_by(Product.name.asc()).all(),
            "sellers": _eligible_sellers(db),
            "payment_methods": {key: value for key, value in PAYMENT_METHODS.items() if key != "invoice"},
            "idempotency_key": str(uuid4()),
        },
    )


def _optional_int(raw: str | None) -> int | None:
    if raw is None or raw == "":
        return None
    return int(raw)


def _parse_sale_lines(form) -> list[SaleLineInput]:
    descriptions = form.getlist("description")
    quantities = form.getlist("quantity")
    unit_prices = form.getlist("unit_price")
    vat_percents = form.getlist("vat_percent")
    discounts = form.getlist("discount_amount")
    product_ids = form.getlist("product_id")
    lines: list[SaleLineInput] = []
    for index, description in enumerate(descriptions):
        if not (description or "").strip() and not (product_ids[index] if index < len(product_ids) else ""):
            continue
        lines.append(
            SaleLineInput(
                product_id=_optional_int(product_ids[index] if index < len(product_ids) else None),
                description=description,
                quantity=quantities[index] if index < len(quantities) else "1",
                unit_price=unit_prices[index] if index < len(unit_prices) else "0",
                vat_percent=vat_percents[index] if index < len(vat_percents) else "24",
                discount_amount=discounts[index] if index < len(discounts) else "0",
            )
        )
    return lines


def _parse_payments(form) -> tuple[list[PaymentInput], bool]:
    methods = form.getlist("payment_method")
    amounts = form.getlist("payment_amount")
    payments: list[PaymentInput] = []
    send_to_invoice = False
    for index, method in enumerate(methods):
        if not method:
            continue
        if method == "invoice":
            send_to_invoice = True
            continue
        amount = amounts[index] if index < len(amounts) else None
        payments.append(PaymentInput(payment_method=method, amount=amount or None))
    if form.get("send_to_invoice") == "true":
        send_to_invoice = True
    return payments, send_to_invoice


@router.post("/quick")
async def create_quick_sale(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    current_user = request_current_user(request)
    try:
        sale = create_sale_from_lines(
            db,
            shift_id=int(form["shift_id"]),
            seller_id=int(form["seller_id"]),
            lines=_parse_sale_lines(form),
            payments=_parse_payments(form)[0],
            created_by_user_id=current_user.id if current_user is not None else None,
            seller_selection_mode="selectable_active_seller",
            source_type="pos",
            send_to_invoice=_parse_payments(form)[1],
            idempotency_key=(form.get("idempotency_key") or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale.id}", status_code=303)


@router.get("/invoice-queue", response_class=HTMLResponse)
def invoice_queue(request: Request, db: Session = Depends(get_db)):
    sales = (
        db.query(Sale)
        .filter(
            or_(
                Sale.settlement_status.in_(list(INVOICE_ACTIVE_STATUSES)),
                Sale.payment_method == "invoice",
                Sale.external_invoice_number.is_not(None),
            )
        )
        .order_by(Sale.due_date.asc(), Sale.next_follow_up_at.asc(), Sale.sold_at.desc())
        .all()
    )
    return templates.TemplateResponse(
        "sales/invoice_queue.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "sales": sales,
            "follow_up_status": invoice_follow_up_status,
        },
    )


@router.get("/work-orders/{work_order_id}", response_class=HTMLResponse)
def work_order_sale_form(work_order_id: int, request: Request, db: Session = Depends(get_db)):
    work_order = db.get(Job, work_order_id)
    if work_order is None:
        raise HTTPException(status_code=404, detail="Work Order not found")
    return templates.TemplateResponse(
        "sales/work_order_conversion.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "work_order": work_order,
            "shifts": db.query(Shift).filter(Shift.status == "open").order_by(Shift.opened_at.desc()).all(),
            "sellers": _eligible_sellers(db),
            "payment_methods": {key: value for key, value in PAYMENT_METHODS.items() if key != "invoice"},
            "existing_sale": next((sale for sale in work_order.sales if sale.status != "cancelled"), None),
            "idempotency_key": f"work-order:{work_order.id}",
        },
    )


@router.post("/work-orders/{work_order_id}")
async def create_work_order_sale(work_order_id: int, request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    current_user = request_current_user(request)
    payments, send_to_invoice = _parse_payments(form)
    try:
        sale = create_sale_from_work_order(
            db,
            work_order_id=work_order_id,
            shift_id=int(form["shift_id"]),
            seller_id=int(form["seller_id"]),
            payments=payments,
            created_by_user_id=current_user.id if current_user is not None else None,
            seller_selection_mode="selectable_active_seller",
            send_to_invoice=send_to_invoice,
            idempotency_key=(form.get("idempotency_key") or "").strip() or None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale.id}", status_code=303)


@router.post("")
def create_sale(
    request: Request,
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
    current_user = request_current_user(request)
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
            created_by_user_id=current_user.id if current_user is not None else None,
            seller_selection_mode="selectable_active_seller",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale.id}", status_code=303)


@router.post("/{sale_id}/seller")
def correct_sale_seller_route(
    sale_id: int,
    request: Request,
    sold_by_user_id: int = Form(...),
    reason: str = Form(...),
    db: Session = Depends(get_db),
):
    current_user = request_current_user(request)
    if not user_can_override_sale_seller(current_user):
        raise HTTPException(status_code=403, detail="Only Admin or Manager can correct sale seller attribution")
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
            "sellers": db.query(User).filter(User.is_active.is_(True)).order_by(User.name.asc()).all(),
            "can_correct_seller": user_can_override_sale_seller(request_current_user(request)),
            "payment_methods": PAYMENT_METHODS,
            "remaining_refundable": remaining_refundable_amount(sale),
            "paid_amount": sale_paid_amount(sale),
            "balance_due": sale_balance_due(sale),
            "invoice_follow_up_status": invoice_follow_up_status(sale),
        },
    )


@router.post("/{sale_id}/invoice-transfer")
def transfer_invoice_route(
    sale_id: int,
    request: Request,
    external_invoice_service: str = Form(...),
    external_invoice_number: str = Form(...),
    invoice_date: str = Form(...),
    due_date: str = Form(...),
    external_invoice_reference: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = request_current_user(request)
    try:
        transfer_sale_to_invoicing(
            db,
            sale_id=sale_id,
            service_name=external_invoice_service,
            external_invoice_number=external_invoice_number,
            invoice_date_value=invoice_date,
            due_date_value=due_date,
            external_reference=external_invoice_reference,
            notes=notes,
            actor_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale_id}", status_code=303)


@router.post("/{sale_id}/invoice-paid")
def invoice_paid_route(
    sale_id: int,
    request: Request,
    payment_date: str = Form(...),
    received_amount: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = request_current_user(request)
    try:
        confirm_invoice_paid(
            db,
            sale_id=sale_id,
            payment_date_value=payment_date,
            received_amount=received_amount or None,
            notes=notes,
            actor_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale_id}", status_code=303)


@router.post("/{sale_id}/invoice-unpaid")
def invoice_unpaid_route(
    sale_id: int,
    request: Request,
    checked_date: str = Form(""),
    next_follow_up_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = request_current_user(request)
    try:
        confirm_invoice_unpaid(
            db,
            sale_id=sale_id,
            checked_date_value=checked_date or None,
            next_follow_up_date_value=next_follow_up_date or None,
            notes=notes,
            actor_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale_id}", status_code=303)


@router.post("/{sale_id}/invoice-reminder")
def invoice_reminder_route(
    sale_id: int,
    request: Request,
    reminder_date: str = Form(...),
    next_follow_up_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
):
    current_user = request_current_user(request)
    try:
        record_invoice_reminder_sent(
            db,
            sale_id=sale_id,
            reminder_date_value=reminder_date,
            next_follow_up_date_value=next_follow_up_date or None,
            notes=notes,
            actor_user_id=current_user.id if current_user else None,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale_id}", status_code=303)


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
            "sale": sale,
            "paid_amount": sale_paid_amount(sale),
            "balance_due": sale_balance_due(sale),
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
