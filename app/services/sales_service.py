import json
from collections import defaultdict
from datetime import UTC, date, datetime, time
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    CashMovement,
    DailyClosing,
    DailyClosingSnapshot,
    Payment,
    Refund,
    Role,
    Sale,
    SaleLine,
    Shift,
    User,
    utc_now,
)
from app.services.audit_service import log_audit_event
from app.services.money_service import money, parse_decimal, sum_money, vat_included_breakdown


ROLE_DEFINITIONS = {
    "admin": "Admin",
    "manager": "Manager",
    "seller": "Seller",
    "read_only": "Read only",
}

PAYMENT_METHODS = {
    "cash": "Cash",
    "card": "Card",
    "bank_transfer": "Bank transfer",
    "mobile": "Mobile",
    "other": "Other",
}


def format_decimal_key(value) -> str:
    text = format(parse_decimal(value).normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def ensure_default_roles(db: Session) -> list[Role]:
    for code, name in ROLE_DEFINITIONS.items():
        if db.query(Role).filter(Role.code == code).first() is None:
            db.add(Role(code=code, name=name))
    db.commit()
    return db.query(Role).order_by(Role.id.asc()).all()


def user_can_manage_closing(user: User | None) -> bool:
    return bool(user and user.role and user.role.code in {"admin", "manager"})


def open_shift(
    db: Session,
    *,
    seller_id: int,
    cash_register_id: int,
    business_date: date,
    starting_cash,
    notes: str = "",
) -> Shift:
    seller = db.get(User, seller_id)
    if seller is None or not seller.is_active:
        raise ValueError("Active seller is required.")
    existing_seller_shift = (
        db.query(Shift)
        .filter(Shift.seller_id == seller_id, Shift.status == "open")
        .first()
    )
    if existing_seller_shift is not None:
        raise ValueError("Seller already has an open shift.")
    existing_register_shift = (
        db.query(Shift)
        .filter(Shift.cash_register_id == cash_register_id, Shift.status == "open")
        .first()
    )
    if existing_register_shift is not None:
        raise ValueError("Cash register already has an open shift.")

    shift = Shift(
        seller_id=seller_id,
        cash_register_id=cash_register_id,
        business_date=business_date,
        starting_cash=money(parse_decimal(starting_cash)),
        notes=notes.strip() or None,
    )
    db.add(shift)
    log_audit_event(
        db,
        event_type="shift.opened",
        entity_type="shift",
        entity_id=None,
        description=f"Shift opened for seller {seller.name}.",
    )
    db.commit()
    db.refresh(shift)
    return shift


def create_sale_with_payment(
    db: Session,
    *,
    seller_id: int,
    shift_id: int,
    payment_method: str,
    description: str,
    quantity,
    unit_price,
    vat_percent,
    discount_amount=0,
    work_order_id: int | None = None,
    product_id: int | None = None,
    work_order_item_id: int | None = None,
) -> Sale:
    shift = db.get(Shift, shift_id)
    if shift is None or shift.status != "open":
        raise ValueError("Sale requires an open shift.")
    if shift.seller_id != seller_id:
        raise ValueError("Sale seller must match shift seller.")
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Invalid payment method.")

    parsed_quantity = parse_decimal(quantity, "1")
    parsed_unit_price = parse_decimal(unit_price)
    parsed_vat_percent = parse_decimal(vat_percent, "24")
    parsed_discount = parse_decimal(discount_amount)
    gross_before_discount = parsed_quantity * parsed_unit_price
    line_total = money(gross_before_discount - parsed_discount)
    if line_total < Decimal("0"):
        raise ValueError("Discount cannot exceed line total.")
    _, vat_amount = vat_included_breakdown(line_total, parsed_vat_percent)
    subtotal = money(line_total - vat_amount)

    vat_breakdown = {
        format_decimal_key(parsed_vat_percent): {
            "gross": str(line_total),
            "net": str(subtotal),
            "vat": str(vat_amount),
        }
    }
    sale = Sale(
        seller_id=seller_id,
        shift_id=shift_id,
        work_order_id=work_order_id,
        payment_method=payment_method,
        subtotal=subtotal,
        vat_total=vat_amount,
        discount_total=money(parsed_discount),
        total=line_total,
        vat_breakdown_json=json.dumps(vat_breakdown, sort_keys=True),
    )
    db.add(sale)
    db.flush()
    db.add(
        SaleLine(
            sale_id=sale.id,
            work_order_item_id=work_order_item_id,
            product_id=product_id,
            description_snapshot=description.strip(),
            quantity=parsed_quantity,
            unit_price=parsed_unit_price,
            vat_percent=parsed_vat_percent,
            discount_amount=money(parsed_discount),
            line_total=line_total,
            vat_amount=vat_amount,
        )
    )
    db.add(
        Payment(
            sale_id=sale.id,
            shift_id=shift_id,
            seller_id=seller_id,
            payment_method=payment_method,
            amount=line_total,
        )
    )
    log_audit_event(
        db,
        event_type="sale.created",
        entity_type="sale",
        entity_id=sale.id,
        description=f"Sale created for {line_total}.",
    )
    db.commit()
    db.refresh(sale)
    return sale


def add_refund(
    db: Session,
    *,
    sale_id: int,
    seller_id: int,
    amount,
    payment_method: str,
    reason: str = "",
) -> Refund:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise ValueError("Sale not found.")
    if sale.seller_id != seller_id:
        raise ValueError("Refund seller must match sale seller.")
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Invalid payment method.")
    parsed_amount = money(parse_decimal(amount))
    if parsed_amount <= 0:
        raise ValueError("Refund amount must be positive.")
    vat_rate = sale.lines[0].vat_percent if sale.lines else Decimal("24")
    _, vat_amount = vat_included_breakdown(parsed_amount, parse_decimal(vat_rate, "24"))
    refund = Refund(
        sale_id=sale.id,
        shift_id=sale.shift_id,
        seller_id=seller_id,
        amount=parsed_amount,
        vat_amount=vat_amount,
        payment_method=payment_method,
        reason=reason.strip() or None,
    )
    sale.status = "refunded" if parsed_amount >= parse_decimal(sale.total) else "partially_refunded"
    db.add(refund)
    log_audit_event(
        db,
        event_type="sale.refunded",
        entity_type="sale",
        entity_id=sale.id,
        description=f"Refund recorded for {parsed_amount}.",
    )
    db.commit()
    db.refresh(refund)
    return refund


def calculate_expected_cash(db: Session, shift: Shift) -> Decimal:
    cash_payments = sum_money(
        payment.amount for payment in shift.payments if payment.payment_method == "cash"
    )
    cash_refunds = sum_money(
        refund.amount for refund in shift.refunds if refund.payment_method == "cash"
    )
    cash_in = sum_money(
        movement.amount
        for movement in shift.cash_movements
        if movement.movement_type == "cash_in"
    )
    cash_out = sum_money(
        movement.amount
        for movement in shift.cash_movements
        if movement.movement_type == "cash_out"
    )
    return money(parse_decimal(shift.starting_cash) + cash_payments - cash_refunds + cash_in - cash_out)


def add_cash_movement(
    db: Session,
    *,
    shift_id: int,
    seller_id: int,
    movement_type: str,
    amount,
    reason: str = "",
) -> CashMovement:
    shift = db.get(Shift, shift_id)
    if shift is None or shift.status != "open":
        raise ValueError("Cash movement requires an open shift.")
    if shift.seller_id != seller_id:
        raise ValueError("Cash movement seller must match shift seller.")
    if movement_type not in {"cash_in", "cash_out"}:
        raise ValueError("Invalid cash movement type.")
    movement = CashMovement(
        shift_id=shift_id,
        seller_id=seller_id,
        movement_type=movement_type,
        amount=money(parse_decimal(amount)),
        reason=reason.strip() or None,
    )
    db.add(movement)
    db.commit()
    db.refresh(movement)
    return movement


def close_shift(db: Session, *, shift_id: int, counted_cash, notes: str = "") -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None or shift.status != "open":
        raise ValueError("Only open shifts can be closed.")
    counted = money(parse_decimal(counted_cash))
    expected = calculate_expected_cash(db, shift)
    shift.counted_closing_cash = counted
    shift.expected_closing_cash = expected
    shift.cash_over_short = money(counted - expected)
    shift.closed_at = utc_now()
    shift.status = "closed"
    shift.notes = notes.strip() or shift.notes
    log_audit_event(
        db,
        event_type="shift.closed",
        entity_type="shift",
        entity_id=shift.id,
        description=f"Shift closed with over/short {shift.cash_over_short}.",
    )
    db.commit()
    db.refresh(shift)
    return shift


def build_daily_closing_snapshot(db: Session, business_date: date) -> dict:
    shifts = db.query(Shift).filter(Shift.business_date == business_date).all()
    shift_ids = [shift.id for shift in shifts]
    sales = db.query(Sale).filter(Sale.shift_id.in_(shift_ids)).all() if shift_ids else []
    refunds = db.query(Refund).filter(Refund.shift_id.in_(shift_ids)).all() if shift_ids else []
    payments = db.query(Payment).filter(Payment.shift_id.in_(shift_ids)).all() if shift_ids else []

    vat_totals = defaultdict(lambda: {"gross": Decimal("0"), "vat": Decimal("0")})
    seller_totals = defaultdict(lambda: Decimal("0"))
    payment_totals = defaultdict(lambda: Decimal("0"))
    discount_total = Decimal("0")

    for sale in sales:
        seller_totals[sale.seller.name if sale.seller else str(sale.seller_id)] += parse_decimal(sale.total)
        discount_total += parse_decimal(sale.discount_total)
        for line in sale.lines:
            rate = format_decimal_key(line.vat_percent)
            vat_totals[rate]["gross"] += parse_decimal(line.line_total)
            vat_totals[rate]["vat"] += parse_decimal(line.vat_amount)

    for payment in payments:
        payment_totals[payment.payment_method] += parse_decimal(payment.amount)

    expected_cash = sum_money(shift.expected_closing_cash or calculate_expected_cash(db, shift) for shift in shifts)
    counted_cash = sum_money(shift.counted_closing_cash or 0 for shift in shifts)
    total_sales = sum_money(sale.total for sale in sales)
    total_refunds = sum_money(refund.amount for refund in refunds)

    return {
        "business_date": business_date.isoformat(),
        "shift_count": len(shifts),
        "sale_count": len(sales),
        "total_sales": str(total_sales),
        "total_refunds": str(total_refunds),
        "total_discounts": str(money(discount_total)),
        "expected_cash": str(expected_cash),
        "counted_cash": str(counted_cash),
        "cash_over_short": str(money(counted_cash - expected_cash)),
        "vat_totals": {
            rate: {"gross": str(money(values["gross"])), "vat": str(money(values["vat"]))}
            for rate, values in vat_totals.items()
        },
        "payment_totals": {
            method: str(money(total)) for method, total in payment_totals.items()
        },
        "seller_totals": {
            seller: str(money(total)) for seller, total in seller_totals.items()
        },
    }


def create_daily_closing(db: Session, *, business_date: date, created_by_user_id: int) -> DailyClosing:
    creator = db.get(User, created_by_user_id)
    if not user_can_manage_closing(creator):
        raise ValueError("Only Admin or Manager can close the day.")
    existing = db.query(DailyClosing).filter(DailyClosing.business_date == business_date).first()
    if existing and existing.status == "closed":
        return existing
    snapshot = build_daily_closing_snapshot(db, business_date)
    closing = existing or DailyClosing(business_date=business_date, created_by_user_id=created_by_user_id)
    closing.created_by_user_id = created_by_user_id
    closing.closed_at = utc_now()
    closing.status = "closed"
    closing.total_sales = parse_decimal(snapshot["total_sales"])
    closing.total_refunds = parse_decimal(snapshot["total_refunds"])
    closing.total_discounts = parse_decimal(snapshot["total_discounts"])
    closing.expected_cash = parse_decimal(snapshot["expected_cash"])
    closing.counted_cash = parse_decimal(snapshot["counted_cash"])
    closing.cash_over_short = parse_decimal(snapshot["cash_over_short"])
    db.add(closing)
    db.flush()
    db.add(
        DailyClosingSnapshot(
            daily_closing_id=closing.id,
            snapshot_json=json.dumps(snapshot, sort_keys=True),
        )
    )
    log_audit_event(
        db,
        event_type="daily_closing.closed",
        entity_type="daily_closing",
        entity_id=closing.id,
        description=f"Daily closing created for {business_date}.",
    )
    db.commit()
    db.refresh(closing)
    return closing


def reopen_daily_closing(db: Session, *, closing_id: int, user_id: int) -> DailyClosing:
    user = db.get(User, user_id)
    if not user_can_manage_closing(user):
        raise ValueError("Only Admin or Manager can reopen a daily closing.")
    closing = db.get(DailyClosing, closing_id)
    if closing is None:
        raise ValueError("Daily closing not found.")
    closing.status = "reopened"
    closing.reopened_at = utc_now()
    closing.reopened_by_user_id = user_id
    log_audit_event(
        db,
        event_type="daily_closing.reopened",
        entity_type="daily_closing",
        entity_id=closing.id,
        description=f"Daily closing reopened for {closing.business_date}.",
    )
    db.commit()
    db.refresh(closing)
    return closing


def seller_report(db: Session, *, seller_id: int, start_date: date, end_date: date) -> dict:
    start_at = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_at = datetime.combine(end_date, time.min, tzinfo=UTC)
    sales = (
        db.query(Sale)
        .filter(Sale.seller_id == seller_id)
        .filter(Sale.sold_at >= start_at)
        .filter(Sale.sold_at < end_at)
        .all()
    )
    refunds = (
        db.query(Refund)
        .filter(Refund.seller_id == seller_id)
        .filter(Refund.refunded_at >= start_at)
        .filter(Refund.refunded_at < end_at)
        .all()
    )
    payment_totals = defaultdict(lambda: Decimal("0"))
    for sale in sales:
        for payment in sale.payments:
            payment_totals[payment.payment_method] += parse_decimal(payment.amount)
    total_sales = sum_money(sale.total for sale in sales)
    transaction_count = len(sales)
    return {
        "total_sales": total_sales,
        "transaction_count": transaction_count,
        "average_sale": money(total_sales / transaction_count) if transaction_count else Decimal("0.00"),
        "discounts": sum_money(sale.discount_total for sale in sales),
        "refunds": sum_money(refund.amount for refund in refunds),
        "payment_totals": {method: money(total) for method, total in payment_totals.items()},
    }
