from collections import defaultdict
from decimal import Decimal
from uuid import uuid4

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import get_db
from app.models import CashRegister, Customer, Job, Product, Sale, Shift, User
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
from app.services.invoice_query_service import (
    INVOICE_QUEUE_VIEWS,
    build_invoice_tabs,
    filter_invoice_sales,
    invoice_related_sales,
    invoice_row,
)
from app.services.settings_service import get_app_settings
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


def _quick_sale_context(request: Request, db: Session, *, error: str | None = None, idempotency_key: str | None = None):
    products = db.query(Product).filter(Product.is_active.is_(True)).order_by(Product.name.asc()).all()
    product_options = []
    for product in products:
        stock_quantity = product.current_inventory_quantity or 0
        product_options.append(
            {
                "product": product,
                "stock_quantity": stock_quantity,
                "out_of_stock": bool(product.is_stock_item and stock_quantity <= 0),
            }
        )
    return {
        "request": request,
        "app_name": settings.app_name,
        "active_page": "sales",
        "shifts": db.query(Shift).filter(Shift.status == "open").order_by(Shift.opened_at.desc()).all(),
        "product_options": product_options,
        "customers": db.query(Customer).order_by(Customer.name.asc()).all(),
        "sellers": _eligible_sellers(db),
        "cash_registers": db.query(CashRegister).filter(CashRegister.is_active.is_(True)).order_by(CashRegister.name.asc()).all(),
        "payment_methods": {key: value for key, value in PAYMENT_METHODS.items() if key != "invoice"},
        "idempotency_key": idempotency_key or str(uuid4()),
        "error": error,
    }


def _format_money(value, language: str) -> str:
    amount = Decimal(str(value or "0")).quantize(Decimal("0.01"))
    text = f"{amount:,.2f}"
    if language == "fi":
        text = text.replace(",", "X").replace(".", ",").replace("X", " ")
        return f"{text} €"
    return f"€{text}"


def _format_decimal(value, *, places: int = 3) -> str:
    amount = Decimal(str(value or "0")).quantize(Decimal("1." + ("0" * places)))
    return f"{amount.normalize():f}"


def _format_vat_rate(value, language: str) -> str:
    amount = Decimal(str(value or "0")).quantize(Decimal("0.01")).normalize()
    text = f"{amount:f}"
    if language == "fi":
        text = text.replace(".", ",")
    return f"{text} %"


def _format_receipt_datetime(value, language: str) -> str:
    if value is None:
        return ""
    if language == "fi":
        return f"{value.day}.{value.month}.{value.year} klo {value:%H.%M}"
    return value.strftime("%Y-%m-%d %H:%M")


def _sale_receipt_context(sale: Sale, db: Session) -> dict:
    settings_values = get_app_settings(db)
    language = settings_values.get("language", "en")
    company = {
        "name": (settings_values.get("company_name") or "").strip(),
        "business_id": (settings_values.get("company_business_id") or "").strip(),
        "address": (settings_values.get("company_address") or "").strip(),
        "phone": (settings_values.get("company_phone") or "").strip(),
        "email": (settings_values.get("company_email") or "").strip(),
    }
    company = {key: value for key, value in company.items() if value}

    receipt_lines = []
    vat_groups = defaultdict(lambda: {"net": Decimal("0.00"), "vat": Decimal("0.00"), "gross": Decimal("0.00")})
    for line in sale.lines:
        gross = Decimal(str(line.line_total or "0"))
        vat_amount = Decimal(str(line.vat_amount or "0"))
        net = gross - vat_amount
        rate_key = str(Decimal(str(line.vat_percent or "0")).quantize(Decimal("0.01")))
        vat_groups[rate_key]["net"] += net
        vat_groups[rate_key]["vat"] += vat_amount
        vat_groups[rate_key]["gross"] += gross
        receipt_lines.append(
            {
                "description": line.description_snapshot,
                "quantity": _format_decimal(line.quantity),
                "unit": line.product.unit if line.product and line.product.unit else "",
                "unit_price": _format_money(line.unit_price, language),
                "vat_rate": _format_vat_rate(line.vat_percent, language),
                "line_total": _format_money(line.line_total, language),
            }
        )

    vat_breakdown = [
        {
            "rate": _format_vat_rate(rate, language),
            "net": _format_money(values["net"], language),
            "vat": _format_money(values["vat"], language),
            "gross": _format_money(values["gross"], language),
        }
        for rate, values in sorted(vat_groups.items(), key=lambda item: Decimal(item[0]))
    ]

    paid = sale_paid_amount(sale)
    balance = sale_balance_due(sale)
    change_due = paid - Decimal(str(sale.total or "0"))
    if change_due < 0:
        change_due = Decimal("0.00")
    customer_name = (
        sale.customer.name
        if sale.customer is not None
        else ((sale.customer_name_snapshot or "").strip() or None)
    )
    source_work_order = None
    if sale.work_order is not None:
        source_work_order = sale.work_order.receipt_number or sale.work_order.job_number or str(sale.work_order.id)
    return {
        "receipt_company": company,
        "receipt_lines": receipt_lines,
        "receipt_vat_breakdown": vat_breakdown,
        "receipt_payments": [
            {
                "method": payment.payment_method,
                "amount": _format_money(payment.amount, language),
            }
            for payment in sale.payments
        ],
        "receipt_customer_name": customer_name,
        "receipt_source_work_order": source_work_order,
        "receipt_datetime": _format_receipt_datetime(sale.sold_at, language),
        "receipt_date": _format_receipt_datetime(sale.sold_at, language).split(" klo ")[0] if language == "fi" else sale.sold_at.strftime("%Y-%m-%d"),
        "receipt_total_ex_vat": _format_money(sale.subtotal, language),
        "receipt_total_vat": _format_money(sale.vat_total, language),
        "receipt_total_inc_vat": _format_money(sale.total, language),
        "receipt_paid_total": _format_money(paid, language),
        "receipt_balance_due": _format_money(balance, language),
        "receipt_balance_due_raw": balance,
        "receipt_change_due": _format_money(change_due, language),
        "receipt_change_due_raw": change_due,
    }


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
    return RedirectResponse(url="/sales/quick", status_code=303)


@router.get("/quick", response_class=HTMLResponse)
def quick_sale(request: Request, db: Session = Depends(get_db)):
    return templates.TemplateResponse("sales/quick.html", _quick_sale_context(request, db))


def _optional_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if text == "":
        return None
    return int(text)


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
            shift_id=_optional_int(form.get("shift_id")),
            cash_register_id=_optional_int(form.get("cash_register_id")),
            seller_id=_optional_int(form.get("seller_id")),
            seller_mode=str(form.get("seller_mode") or "default"),
            lines=_parse_sale_lines(form),
            payments=_parse_payments(form)[0],
            created_by_user_id=current_user.id if current_user is not None else None,
            seller_selection_mode="selectable_active_seller",
            source_type="pos",
            send_to_invoice=_parse_payments(form)[1],
            idempotency_key=(form.get("idempotency_key") or "").strip() or None,
            customer_id=_optional_int(form.get("customer_id")),
            customer_name=str(form.get("customer_name") or ""),
        )
    except ValueError as exc:
        return templates.TemplateResponse(
            "sales/quick.html",
            _quick_sale_context(
                request,
                db,
                error=str(exc),
                idempotency_key=(form.get("idempotency_key") or "").strip() or None,
            ),
            status_code=400,
        )
    return RedirectResponse(url=f"/sales/{sale.id}", status_code=303)


@router.get("/invoice-queue", response_class=HTMLResponse)
def invoice_queue(request: Request, view: str = "action_required", db: Session = Depends(get_db)):
    selected_view = view if view in INVOICE_QUEUE_VIEWS else "action_required"
    all_invoice_sales = invoice_related_sales(db)
    sales = filter_invoice_sales(all_invoice_sales, selected_view)
    return templates.TemplateResponse(
        "sales/invoice_queue.html",
        {
            "request": request,
            "app_name": settings.app_name,
            "active_page": "sales",
            "view": selected_view,
            "sales": [invoice_row(sale) for sale in sales],
            "tabs": build_invoice_tabs(all_invoice_sales, active_view=selected_view),
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
            "cash_registers": db.query(CashRegister).filter(CashRegister.is_active.is_(True)).order_by(CashRegister.name.asc()).all(),
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
            shift_id=_optional_int(form.get("shift_id")),
            cash_register_id=_optional_int(form.get("cash_register_id")),
            seller_id=_optional_int(form.get("seller_id")),
            seller_mode=str(form.get("seller_mode") or "default"),
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
    shift_id: str = Form(""),
    cash_register_id: str = Form(""),
    seller_id: str = Form(""),
    seller_mode: str = Form("default"),
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
            seller_id=_optional_int(seller_id),
            shift_id=_optional_int(shift_id),
            cash_register_id=_optional_int(cash_register_id),
            seller_mode=seller_mode,
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
            "payment_methods": {key: value for key, value in PAYMENT_METHODS.items() if key != "invoice"},
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
            "page_title": "Receipt",
            "sale": sale,
            "paid_amount": sale_paid_amount(sale),
            "balance_due": sale_balance_due(sale),
            **_sale_receipt_context(sale, db),
        },
    )


@router.post("/{sale_id}/refunds")
def create_refund(
    sale_id: int,
    request: Request,
    refund_shift_id: str = Form(""),
    amount: str = Form(...),
    payment_method: str = Form(...),
    reason: str = Form(""),
    db: Session = Depends(get_db),
):
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail="Sale not found")

    parsed_refund_shift_id = _optional_int(refund_shift_id)
    refund_shift = db.get(Shift, parsed_refund_shift_id) if parsed_refund_shift_id else None
    if parsed_refund_shift_id and refund_shift is None:
        raise HTTPException(status_code=400, detail="Refund shift not found")
    current_user = request_current_user(request)
    seller_id = refund_shift.seller_id if refund_shift is not None else (
        current_user.id
        if current_user is not None
        else (sale.sold_by_user_id or sale.seller_id or sale.created_by_user_id)
    )
    if seller_id is None:
        raise HTTPException(status_code=400, detail="Refund requires an active operator or sale seller.")
    try:
        add_refund(
            db,
            sale_id=sale_id,
            refund_shift_id=parsed_refund_shift_id,
            seller_id=seller_id,
            amount=amount,
            payment_method=payment_method,
            reason=reason,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return RedirectResponse(url=f"/sales/{sale_id}", status_code=303)
