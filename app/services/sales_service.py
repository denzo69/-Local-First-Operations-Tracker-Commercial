import json
from collections import defaultdict
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import (
    CashRegister,
    CashMovement,
    DailyClosing,
    DailyClosingSnapshot,
    Job,
    JobItem,
    Payment,
    Product,
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
from app.services.settings_service import get_app_settings


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

VAT_PERCENT_MAX = Decimal("100")
SNAPSHOT_SCHEMA_VERSION = 1
OPERATIONAL_ROLE_CODES = {"admin", "manager", "seller"}
CLOSING_MANAGER_ROLE_CODES = {"admin", "manager"}
SALE_SELLER_SELECTION_MODES = {
    "authenticated_user",
    "shift_owner",
    "selectable_active_seller",
}


class AuthorizationError(ValueError):
    pass


def format_decimal_key(value) -> str:
    text = format(parse_decimal(value).normalize(), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def parse_finite_decimal(value, field_name: str, default: str = "0") -> Decimal:
    try:
        parsed = parse_decimal(value, default)
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field_name} must be a valid decimal number.") from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal number.")
    return parsed


def require_non_negative_money(value, field_name: str) -> Decimal:
    parsed = money(parse_finite_decimal(value, field_name))
    if parsed < 0:
        raise ValueError(f"{field_name} cannot be negative.")
    return parsed


def require_positive_money(value, field_name: str) -> Decimal:
    parsed = money(parse_finite_decimal(value, field_name))
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return parsed


def require_positive_quantity(value, field_name: str = "Quantity") -> Decimal:
    parsed = parse_finite_decimal(value, field_name, "1")
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return parsed


def require_vat_percent(value) -> Decimal:
    parsed = parse_finite_decimal(value, "VAT percent", "24")
    if parsed < 0 or parsed > VAT_PERCENT_MAX:
        raise ValueError(f"VAT percent must be between 0 and {VAT_PERCENT_MAX}.")
    return parsed


def require_payment_method(payment_method: str) -> None:
    if payment_method not in PAYMENT_METHODS:
        raise ValueError("Invalid payment method.")


def remaining_refundable_amount(sale: Sale) -> Decimal:
    existing_refunds = sum_money(refund.amount for refund in sale.refunds)
    return money(parse_decimal(sale.total) - existing_refunds)


def require_operational_user(user: User | None) -> User:
    if user is None:
        raise ValueError("User not found.")
    if not user.is_active:
        raise ValueError("Active user is required.")
    if not user.role or user.role.code not in OPERATIONAL_ROLE_CODES:
        raise ValueError("User role is not allowed to perform financial writes.")
    return user


def require_sales_credit_user(user: User | None) -> User:
    user = require_operational_user(user)
    if user.can_receive_sales_credit or (user.role and user.role.code == "seller"):
        return user
    raise ValueError("User is not eligible to receive sales credit.")


def cashier_shift_required(db: Session) -> bool:
    return str(get_app_settings(db).get("require_cashier_shift", "false")).lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def user_can_override_sale_seller(user: User | None) -> bool:
    return bool(user and user.is_active and user.role and user.role.code in {"admin", "manager"})


def require_sale_seller_override_user(user: User | None) -> User:
    if not user_can_override_sale_seller(user):
        raise AuthorizationError("Only Admin or Manager can correct sale seller attribution.")
    return user


def resolve_sale_seller(
    db: Session,
    *,
    shift: Shift | None,
    selected_seller_id: int | None,
    created_by_user_id: int | None,
    seller_selection_mode: str,
) -> tuple[User | None, User | None]:
    mode = seller_selection_mode if seller_selection_mode in SALE_SELLER_SELECTION_MODES else "selectable_active_seller"
    operator = db.get(User, created_by_user_id) if created_by_user_id else None
    if operator is not None:
        require_operational_user(operator)

    if mode == "authenticated_user" and operator is not None:
        return operator, operator
    if selected_seller_id:
        return require_sales_credit_user(db.get(User, selected_seller_id)), operator
    if operator is not None:
        return operator, operator
    if shift is not None:
        return require_sales_credit_user(shift.seller), operator
    return None, operator


def require_closing_manager(user: User | None) -> User:
    if not user_can_manage_closing(user):
        raise ValueError("Only Admin or Manager can manage daily closings.")
    if user is None or not user.is_active:
        raise ValueError("Active Admin or Manager is required.")
    return user


def assert_business_date_open(db: Session, business_date: date) -> None:
    closing = (
        db.query(DailyClosing)
        .filter(DailyClosing.business_date == business_date)
        .first()
    )
    if closing is not None and closing.status == "closed":
        raise ValueError("Business date is closed. Reopen the daily closing before making financial changes.")


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
    require_operational_user(seller)
    register = db.get(CashRegister, cash_register_id)
    if register is None:
        raise ValueError("Cash register not found.")
    if not register.is_active:
        raise ValueError("Active cash register is required.")
    assert_business_date_open(db, business_date)
    parsed_starting_cash = require_non_negative_money(starting_cash, "Starting cash")
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
        starting_cash=parsed_starting_cash,
        notes=notes.strip() or None,
    )
    db.add(shift)
    db.flush()
    log_audit_event(
        db,
        event_type="shift.opened",
        entity_type="shift",
        entity_id=shift.id,
        description=f"Shift opened for seller {seller.name}.",
    )
    db.commit()
    db.refresh(shift)
    return shift


def create_sale_with_payment(
    db: Session,
    *,
    seller_id: int | None = None,
    shift_id: int | None = None,
    payment_method: str,
    description: str,
    quantity,
    unit_price,
    vat_percent,
    discount_amount=0,
    work_order_id: int | None = None,
    product_id: int | None = None,
    work_order_item_id: int | None = None,
    created_by_user_id: int | None = None,
    seller_selection_mode: str = "shift_owner",
) -> Sale:
    shift = db.get(Shift, shift_id) if shift_id else None
    if shift_id and (shift is None or shift.status != "open"):
        raise ValueError("Selected cashier shift must be open.")
    if shift is not None:
        assert_business_date_open(db, shift.business_date)
    elif cashier_shift_required(db):
        raise ValueError("An active cashier shift is required by configuration.")
    sold_by, operator = resolve_sale_seller(
        db,
        shift=shift,
        selected_seller_id=seller_id,
        created_by_user_id=created_by_user_id,
        seller_selection_mode=seller_selection_mode,
    )
    operator_id = operator.id if operator is not None else (sold_by.id if sold_by is not None else None)
    require_payment_method(payment_method)

    work_order = db.get(Job, work_order_id) if work_order_id else None
    if work_order_id and work_order is None:
        raise ValueError("Work Order not found.")
    product = db.get(Product, product_id) if product_id else None
    if product_id and (product is None or not product.is_active):
        raise ValueError("Active product not found.")
    work_order_item = db.get(JobItem, work_order_item_id) if work_order_item_id else None
    if work_order_item_id and work_order_item is None:
        raise ValueError("Work Order item not found.")
    if work_order_item and work_order_id and work_order_item.job_id != work_order_id:
        raise ValueError("Work Order item does not belong to the selected Work Order.")

    description_snapshot = description.strip()
    if not description_snapshot:
        raise ValueError("Description is required.")
    parsed_quantity = require_positive_quantity(quantity)
    parsed_unit_price = require_non_negative_money(unit_price, "Unit price")
    parsed_vat_percent = require_vat_percent(vat_percent)
    parsed_discount = require_non_negative_money(discount_amount, "Discount amount")
    gross_before_discount = parsed_quantity * parsed_unit_price
    if parsed_discount > gross_before_discount:
        raise ValueError("Discount cannot exceed line total.")
    line_total = money(gross_before_discount - parsed_discount)
    _, vat_amount = vat_included_breakdown(line_total, parsed_vat_percent)
    subtotal = money(line_total - vat_amount)

    vat_breakdown = {
        format_decimal_key(parsed_vat_percent): {
            "gross": str(line_total),
            "net": str(subtotal),
            "vat": str(vat_amount),
        }
    }
    try:
        sale = Sale(
            seller_id=sold_by.id if sold_by is not None else None,
            sold_by_user_id=sold_by.id if sold_by is not None else None,
            created_by_user_id=operator_id,
            shift_id=shift.id if shift is not None else None,
            cash_register_id=shift.cash_register_id if shift is not None else None,
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
        sale_line = SaleLine(
            sale_id=sale.id,
            work_order_item_id=work_order_item_id,
            product_id=product_id,
            description_snapshot=description_snapshot,
            quantity=parsed_quantity,
            unit_price=parsed_unit_price,
            vat_percent=parsed_vat_percent,
            discount_amount=money(parsed_discount),
            line_total=line_total,
            vat_amount=vat_amount,
        )
        db.add(sale_line)
        cogs_total = Decimal("0.00")
        if product is not None and product.is_stock_item:
            from app.services.inventory_service import issue_stock_for_sale_from_available_locations

            inventory_transactions = issue_stock_for_sale_from_available_locations(
                db,
                product_id=product.id,
                quantity_value=parsed_quantity,
                sale_id=sale.id,
                created_by_user_id=operator_id,
                commit=False,
            )
            cogs_total = money(
                sum((-parse_decimal(transaction.total_inventory_cost) for transaction in inventory_transactions), Decimal("0"))
            )
        gross_profit = money(subtotal - cogs_total)
        gross_margin_percent = (
            (gross_profit / subtotal * Decimal("100")).quantize(Decimal("0.001"))
            if subtotal > 0
            else None
        )
        sale_line.cost_of_goods_sold_ex_vat = cogs_total
        sale_line.gross_profit_ex_vat = gross_profit
        sale_line.gross_margin_percent = gross_margin_percent
        sale.cost_of_goods_sold_ex_vat = cogs_total
        sale.gross_profit_ex_vat = gross_profit
        sale.gross_margin_percent = gross_margin_percent
        db.add(
            Payment(
                sale_id=sale.id,
                shift_id=shift.id if shift is not None else None,
                seller_id=sold_by.id if sold_by is not None else None,
                received_by_user_id=operator_id,
                payment_method=payment_method,
                amount=line_total,
            )
        )
        log_audit_event(
            db,
            event_type="sale.created",
            entity_type="sale",
            entity_id=sale.id,
            description=(
                f"Sale created for {line_total}; "
                f"sold by {sold_by.id if sold_by else 'unknown'}/{sold_by.name if sold_by else 'Unknown'}; "
                f"operator {operator_id if operator_id is not None else 'unknown'}; "
                f"shift {shift.id if shift else 'none'}; cash register {shift.cash_register_id if shift else 'none'}."
            ),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(sale)
    return sale


def correct_sale_seller(
    db: Session,
    *,
    sale_id: int,
    new_sold_by_user_id: int,
    corrected_by_user_id: int,
    reason: str,
) -> Sale:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise ValueError("Sale not found.")
    if sale.shift is not None:
        assert_business_date_open(db, sale.shift.business_date)
    corrected_by = require_sale_seller_override_user(db.get(User, corrected_by_user_id))
    correction_reason = reason.strip()
    if not correction_reason:
        raise ValueError("Seller correction reason is required.")
    new_seller = require_sales_credit_user(db.get(User, new_sold_by_user_id))
    old_seller_id = sale.sold_by_user_id or sale.seller_id
    old_seller_name = sale.sold_by.name if sale.sold_by else (sale.seller.name if sale.seller else "Unknown")

    sale.sold_by_user_id = new_seller.id
    sale.seller_id = new_seller.id
    sale.seller_override_reason = correction_reason
    sale.seller_overridden_by_user_id = corrected_by.id
    sale.seller_overridden_at = utc_now()
    log_audit_event(
        db,
        event_type="sale.seller_corrected",
        entity_type="sale",
        entity_id=sale.id,
        description=(
            f"Sale seller corrected for sale {sale.id}: "
            f"{old_seller_id}/{old_seller_name} -> {new_seller.id}/{new_seller.name}; "
            f"corrected by {corrected_by.id}/{corrected_by.name}; reason: {correction_reason}."
        ),
    )
    db.commit()
    db.refresh(sale)
    return sale


def add_refund(
    db: Session,
    *,
    sale_id: int,
    refund_shift_id: int,
    seller_id: int,
    amount,
    payment_method: str,
    reason: str = "",
) -> Refund:
    sale = db.get(Sale, sale_id)
    if sale is None:
        raise ValueError("Sale not found.")
    refund_shift = db.get(Shift, refund_shift_id)
    if refund_shift is None or refund_shift.status != "open":
        raise ValueError("Refund requires an open shift.")
    assert_business_date_open(db, refund_shift.business_date)
    seller = require_operational_user(db.get(User, seller_id))
    if refund_shift.seller_id != seller_id:
        raise ValueError("Refund seller must match refund shift seller.")
    require_payment_method(payment_method)
    parsed_amount = require_positive_money(amount, "Refund amount")
    existing_refunds = sum_money(refund.amount for refund in sale.refunds)
    remaining_refundable = remaining_refundable_amount(sale)
    if parsed_amount > remaining_refundable:
        raise ValueError("Refund exceeds remaining refundable sale total.")

    vat_rates = {format_decimal_key(line.vat_percent) for line in sale.lines}
    if len(vat_rates) > 1:
        raise ValueError("Multi-VAT refunds require line-level allocation.")
    if not sale.lines:
        raise ValueError("Sale has no lines to allocate refund VAT.")
    line = sale.lines[0]
    _, vat_amount = vat_included_breakdown(parsed_amount, parse_decimal(line.vat_percent, "24"))
    vat_breakdown = {
        format_decimal_key(line.vat_percent): {
            "gross": str(parsed_amount),
            "vat": str(vat_amount),
        }
    }
    refund = Refund(
        sale_id=sale.id,
        shift_id=refund_shift.id,
        seller_id=seller_id,
        amount=parsed_amount,
        vat_amount=vat_amount,
        vat_breakdown_json=json.dumps(vat_breakdown, sort_keys=True),
        payment_method=payment_method,
        reason=reason.strip() or None,
    )
    db.add(refund)
    db.flush()
    cumulative_refunds = money(existing_refunds + parsed_amount)
    if cumulative_refunds == 0:
        sale.status = "completed"
    elif cumulative_refunds < parse_decimal(sale.total):
        sale.status = "partially_refunded"
    else:
        sale.status = "refunded"
    log_audit_event(
        db,
        event_type="sale.refunded",
        entity_type="refund",
        entity_id=refund.id,
        description=(
            f"Refund recorded for sale {sale.id}: original seller "
            f"{sale.seller_id}/{sale.seller.name if sale.seller else 'Unknown'}, "
            f"refunding seller {seller.id}/{seller.name}, shift {refund_shift.id}, "
            f"business date {refund_shift.business_date}, amount {parsed_amount}."
        ),
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


def decimal_string(value) -> str:
    return str(money(parse_decimal(value)))


def parse_json_object(raw: str | None, context: str) -> dict:
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{context} JSON is invalid.") from exc
    if not isinstance(parsed, dict):
        raise ValueError(f"{context} JSON must be an object.")
    return parsed


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
    assert_business_date_open(db, shift.business_date)
    seller = require_operational_user(db.get(User, seller_id))
    if shift.seller_id != seller_id:
        raise ValueError("Cash movement seller must match shift seller.")
    if movement_type not in {"cash_in", "cash_out"}:
        raise ValueError("Invalid cash movement type.")
    parsed_amount = require_positive_money(amount, "Cash movement amount")
    movement = CashMovement(
        shift_id=shift_id,
        seller_id=seller_id,
        movement_type=movement_type,
        amount=parsed_amount,
        reason=reason.strip() or None,
    )
    db.add(movement)
    db.flush()
    log_audit_event(
        db,
        event_type="cash_movement.created",
        entity_type="cash_movement",
        entity_id=movement.id,
        description=f"{movement_type} recorded for {parsed_amount} by {seller.name}.",
    )
    db.commit()
    db.refresh(movement)
    return movement


def close_shift(db: Session, *, shift_id: int, counted_cash, notes: str = "") -> Shift:
    shift = db.get(Shift, shift_id)
    if shift is None or shift.status != "open":
        raise ValueError("Only open shifts can be closed.")
    assert_business_date_open(db, shift.business_date)
    counted = require_non_negative_money(counted_cash, "Counted cash")
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

    vat_totals = defaultdict(
        lambda: {
            "gross_sales": Decimal("0"),
            "gross_vat": Decimal("0"),
            "refunds": Decimal("0"),
            "refund_vat": Decimal("0"),
        }
    )
    seller_totals = defaultdict(
        lambda: {
            "seller_id": None,
            "seller_name": "",
            "gross_sales": Decimal("0"),
            "refunds": Decimal("0"),
            "discounts": Decimal("0"),
            "transaction_count": 0,
        }
    )
    payment_totals = defaultdict(
        lambda: {
            "gross_received": Decimal("0"),
            "refunds": Decimal("0"),
        }
    )
    discount_total = Decimal("0")
    gross_vat = Decimal("0")
    refund_vat = Decimal("0")

    for sale in sales:
        sale_seller_id = sale.sold_by_user_id or sale.seller_id
        sale_seller = sale.sold_by or sale.seller
        seller_bucket = seller_totals[sale_seller_id]
        seller_bucket["seller_id"] = sale_seller_id
        seller_bucket["seller_name"] = sale_seller.name if sale_seller else "Unknown"
        seller_bucket["gross_sales"] += parse_decimal(sale.total)
        seller_bucket["discounts"] += parse_decimal(sale.discount_total)
        seller_bucket["transaction_count"] += 1
        discount_total += parse_decimal(sale.discount_total)
        for line in sale.lines:
            rate = format_decimal_key(line.vat_percent)
            vat_totals[rate]["gross_sales"] += parse_decimal(line.line_total)
            vat_totals[rate]["gross_vat"] += parse_decimal(line.vat_amount)
            gross_vat += parse_decimal(line.vat_amount)

    for payment in payments:
        payment_totals[payment.payment_method]["gross_received"] += parse_decimal(payment.amount)

    for refund in refunds:
        payment_totals[refund.payment_method]["refunds"] += parse_decimal(refund.amount)
        seller_bucket = seller_totals[refund.seller_id]
        seller_bucket["seller_id"] = refund.seller_id
        seller_bucket["seller_name"] = refund.seller.name if refund.seller else f"User {refund.seller_id}"
        seller_bucket["refunds"] += parse_decimal(refund.amount)
        refund_vat += parse_decimal(refund.vat_amount)
        refund_breakdown = parse_json_object(refund.vat_breakdown_json, "Refund VAT breakdown")
        for rate, values in refund_breakdown.items():
            vat_totals[rate]["refunds"] += parse_decimal(values.get("gross"))
            vat_totals[rate]["refund_vat"] += parse_decimal(values.get("vat"))

    expected_cash = sum_money(shift.expected_closing_cash or calculate_expected_cash(db, shift) for shift in shifts)
    counted_cash = sum_money(shift.counted_closing_cash or 0 for shift in shifts)
    gross_sales = sum_money(sale.total for sale in sales)
    total_refunds = sum_money(refund.amount for refund in refunds)
    net_sales = money(gross_sales - total_refunds)

    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "business_date": business_date.isoformat(),
        "shift_count": len(shifts),
        "sale_count": len(sales),
        "gross_sales": decimal_string(gross_sales),
        "total_sales": decimal_string(gross_sales),
        "total_discounts": decimal_string(discount_total),
        "total_refunds": decimal_string(total_refunds),
        "net_sales": decimal_string(net_sales),
        "gross_vat": decimal_string(gross_vat),
        "refund_vat": decimal_string(refund_vat),
        "net_vat": decimal_string(gross_vat - refund_vat),
        "expected_cash": decimal_string(expected_cash),
        "counted_cash": decimal_string(counted_cash),
        "cash_over_short": decimal_string(counted_cash - expected_cash),
        "vat_totals": [
            {
                "vat_rate": rate,
                "gross_sales": decimal_string(values["gross_sales"]),
                "gross_vat": decimal_string(values["gross_vat"]),
                "refunds": decimal_string(values["refunds"]),
                "refund_vat": decimal_string(values["refund_vat"]),
                "net_sales": decimal_string(values["gross_sales"] - values["refunds"]),
                "net_vat": decimal_string(values["gross_vat"] - values["refund_vat"]),
            }
            for rate, values in vat_totals.items()
        ],
        "payment_totals": [
            {
                "payment_method": method,
                "gross_received": decimal_string(values["gross_received"]),
                "refunds": decimal_string(values["refunds"]),
                "net": decimal_string(values["gross_received"] - values["refunds"]),
            }
            for method, values in payment_totals.items()
        ],
        "seller_totals": [
            {
                "seller_id": values["seller_id"],
                "seller_name": values["seller_name"],
                "gross_sales": decimal_string(values["gross_sales"]),
                "refunds": decimal_string(values["refunds"]),
                "net_sales": decimal_string(values["gross_sales"] - values["refunds"]),
                "transaction_count": values["transaction_count"],
                "average_sale": decimal_string(
                    values["gross_sales"] / values["transaction_count"]
                    if values["transaction_count"]
                    else Decimal("0")
                ),
                "discounts": decimal_string(values["discounts"]),
            }
            for values in seller_totals.values()
        ],
    }


def create_daily_closing(db: Session, *, business_date: date, created_by_user_id: int) -> DailyClosing:
    creator = require_closing_manager(db.get(User, created_by_user_id))
    open_shifts = (
        db.query(Shift)
        .filter(Shift.business_date == business_date, Shift.status == "open")
        .order_by(Shift.id.asc())
        .all()
    )
    if open_shifts:
        shift_list = ", ".join(str(shift.id) for shift in open_shifts)
        raise ValueError(f"Cannot close day while {len(open_shifts)} shift(s) are open: {shift_list}.")
    existing = db.query(DailyClosing).filter(DailyClosing.business_date == business_date).first()
    if existing and existing.status == "closed":
        raise ValueError("Daily closing is already closed.")
    snapshot = build_daily_closing_snapshot(db, business_date)
    closing = existing or DailyClosing(business_date=business_date, created_by_user_id=created_by_user_id)
    next_version = (closing.current_version or 0) + 1
    snapshot["version"] = next_version
    snapshot["closed_by_user_id"] = created_by_user_id
    snapshot["closed_by_name"] = creator.name
    closing.created_by_user_id = created_by_user_id
    closing.closed_at = utc_now()
    closing.status = "closed"
    closing.current_version = next_version
    closing.total_sales = parse_decimal(snapshot["gross_sales"])
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
            version=next_version,
            schema_version=SNAPSHOT_SCHEMA_VERSION,
            created_by_user_id=created_by_user_id,
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


def parse_daily_closing_snapshot(snapshot: DailyClosingSnapshot) -> dict:
    parsed = parse_json_object(snapshot.snapshot_json, "Daily closing snapshot")
    if parsed.get("schema_version") != snapshot.schema_version:
        raise ValueError("Daily closing snapshot schema version mismatch.")
    return parsed


def get_latest_daily_closing_snapshot(db: Session, closing: DailyClosing) -> tuple[DailyClosingSnapshot, dict]:
    snapshot = (
        db.query(DailyClosingSnapshot)
        .filter(DailyClosingSnapshot.daily_closing_id == closing.id)
        .order_by(DailyClosingSnapshot.version.desc(), DailyClosingSnapshot.id.desc())
        .first()
    )
    if snapshot is None:
        raise ValueError("Daily closing has no stored snapshot.")
    return snapshot, parse_daily_closing_snapshot(snapshot)


def get_daily_closing_snapshot_by_version(
    db: Session,
    closing: DailyClosing,
    version: int,
) -> tuple[DailyClosingSnapshot, dict]:
    snapshot = (
        db.query(DailyClosingSnapshot)
        .filter(
            DailyClosingSnapshot.daily_closing_id == closing.id,
            DailyClosingSnapshot.version == version,
        )
        .first()
    )
    if snapshot is None:
        raise ValueError("Daily closing snapshot version not found.")
    return snapshot, parse_daily_closing_snapshot(snapshot)


def reopen_daily_closing(db: Session, *, closing_id: int, user_id: int, reason: str) -> DailyClosing:
    user = require_closing_manager(db.get(User, user_id))
    closing = db.get(DailyClosing, closing_id)
    if closing is None:
        raise ValueError("Daily closing not found.")
    if closing.status != "closed":
        raise ValueError("Only a closed daily closing can be reopened.")
    reopen_reason = reason.strip()
    if not reopen_reason:
        raise ValueError("Reopen reason is required.")
    closing.status = "reopened"
    closing.reopened_at = utc_now()
    closing.reopened_by_user_id = user_id
    closing.reopen_reason = reopen_reason
    log_audit_event(
        db,
        event_type="daily_closing.reopened",
        entity_type="daily_closing",
        entity_id=closing.id,
        description=f"Daily closing reopened for {closing.business_date} by {user.name}: {reopen_reason}.",
    )
    db.commit()
    db.refresh(closing)
    return closing


def seller_report(db: Session, *, seller_id: int, start_date: date, end_date: date) -> dict:
    start_at = datetime.combine(start_date, time.min, tzinfo=UTC)
    end_at = datetime.combine(end_date, time.min, tzinfo=UTC)
    sales = (
        db.query(Sale)
        .filter(((Sale.sold_by_user_id == seller_id) | ((Sale.sold_by_user_id.is_(None)) & (Sale.seller_id == seller_id))))
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
    total_refunds = sum_money(refund.amount for refund in refunds)
    transaction_count = len(sales)
    net_sales = money(total_sales - total_refunds)
    return {
        "gross_sales": total_sales,
        "total_sales": total_sales,
        "net_sales": net_sales,
        "transaction_count": transaction_count,
        "average_sale": money(total_sales / transaction_count) if transaction_count else Decimal("0.00"),
        "discounts": sum_money(sale.discount_total for sale in sales),
        "refunds": total_refunds,
        "payment_totals": {method: money(total) for method, total in payment_totals.items()},
    }
