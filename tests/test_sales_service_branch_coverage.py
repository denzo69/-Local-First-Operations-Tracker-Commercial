from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from app.database import SessionLocal
from app.models import CashRegister, Customer, DailyClosing, Job, JobItem, JobStatus, Role, Sale, Shift, User
from app.services.auth_service import hash_password
from app.services.sales_service import (
    INVOICE_PAYMENT_METHOD,
    PaymentInput,
    SaleLineInput,
    _customer_snapshot_for_invoice,
    _normalize_line_input,
    _normalize_payment_input,
    _parse_date_value,
    _sale_payment_method_label,
    _validate_sale_source,
    assert_business_date_open,
    cashier_shift_required,
    create_sale_from_lines,
    create_sale_from_work_order,
    ensure_default_roles,
    invoice_follow_up_alerts,
    invoice_follow_up_status,
    parse_json_object,
    require_closing_manager,
    require_daily_closing_creator,
    require_invoice_sale,
    resolve_sale_context,
    settlement_status_for,
)
from app.services.settings_service import set_app_settings


def _user(db, name: str, role_code: str = "admin", *, active: bool = True, credit: bool = True) -> User:
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    user = User(
        name=name,
        login_name=name.lower().replace(" ", "."),
        password_hash=hash_password("secret123"),
        role=role,
        is_active=active,
        can_receive_sales_credit=credit,
    )
    db.add(user)
    db.flush()
    return user


def test_sale_context_optional_shift_seller_and_cash_register_edges():
    with SessionLocal() as db:
        operator = _user(db, "Operator", "manager", credit=False)
        seller = _user(db, "Seller", "seller")
        register = CashRegister(name="Desk", is_active=True)
        inactive_register = CashRegister(name="Closed desk", is_active=False)
        db.add_all([register, inactive_register])
        db.flush()
        shift = Shift(cash_register=register, seller=seller, business_date=date.today(), status="open")
        db.add(shift)
        db.commit()

        none_context = resolve_sale_context(
            db,
            shift_id=None,
            cash_register_id=register.id,
            seller_mode="none",
            selected_seller_id=None,
            created_by_user_id=operator.id,
            seller_selection_mode="selectable_active_seller",
        )
        selected_context = resolve_sale_context(
            db,
            shift_id=shift.id,
            cash_register_id=inactive_register.id,
            seller_mode="selected",
            selected_seller_id=seller.id,
            created_by_user_id=operator.id,
            seller_selection_mode="shift_owner",
        )

        assert none_context.sold_by is None
        assert none_context.cash_register_id == register.id
        assert selected_context.sold_by.id == seller.id
        assert selected_context.cash_register_id == register.id

        with pytest.raises(ValueError, match="Active cash register"):
            resolve_sale_context(
                db,
                shift_id=None,
                cash_register_id=inactive_register.id,
                seller_mode="default",
                selected_seller_id=None,
                created_by_user_id=operator.id,
                seller_selection_mode="shift_owner",
            )
        with pytest.raises(ValueError, match="Select a credited seller"):
            resolve_sale_context(
                db,
                shift_id=None,
                cash_register_id=None,
                seller_mode="selected",
                selected_seller_id=None,
                created_by_user_id=operator.id,
                seller_selection_mode="shift_owner",
            )


def test_sales_service_pure_validation_edges():
    assert _normalize_line_input({"description": "Manual", "quantity": "1", "unit_price": "1", "vat_percent": "24"}).description == "Manual"
    assert _normalize_payment_input({"payment_method": "cash", "amount": "1"}).payment_method == "cash"
    assert _sale_payment_method_label([PaymentInput("invoice")], True) == INVOICE_PAYMENT_METHOD
    assert _sale_payment_method_label([PaymentInput("cash"), PaymentInput("card")], False) == "mixed"
    assert _validate_sale_source("") == "pos"
    with pytest.raises(ValueError, match="Invalid sale source"):
        _validate_sale_source("legacy")
    with pytest.raises(ValueError, match="Payment total cannot exceed"):
        settlement_status_for(total=Decimal("1.00"), paid=Decimal("2.00"), invoice_requested=False)
    assert settlement_status_for(total=Decimal("0.00"), paid=Decimal("0.00"), invoice_requested=False) == "unpaid"
    assert _parse_date_value("", "Optional") is None
    assert _parse_date_value(date(2026, 1, 1), "Date") == date(2026, 1, 1)
    with pytest.raises(ValueError, match="Required is required"):
        _parse_date_value("", "Required", required=True)
    with pytest.raises(ValueError, match="Bad must be a valid date"):
        _parse_date_value("not-date", "Bad")
    assert parse_json_object("", "Context") == {}
    with pytest.raises(ValueError, match="Context JSON is invalid"):
        parse_json_object("{", "Context")
    with pytest.raises(ValueError, match="Context JSON must be an object"):
        parse_json_object("[]", "Context")


def test_invoice_followup_and_daily_closing_guard_edges():
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        inactive_manager = _user(db, "Inactive Manager", "manager", active=False)
        invoice_sale = Sale(
            payment_method="invoice",
            settlement_status="transferred_to_invoicing",
            total=Decimal("10.00"),
            subtotal=Decimal("8.00"),
            vat_total=Decimal("2.00"),
            due_date=date.today() - timedelta(days=1),
        )
        reminder_sale = Sale(
            payment_method="invoice",
            settlement_status="unpaid",
            total=Decimal("10.00"),
            subtotal=Decimal("8.00"),
            vat_total=Decimal("2.00"),
            next_follow_up_at=datetime.combine(date.today(), datetime.min.time(), tzinfo=UTC),
        )
        closed = DailyClosing(business_date=date.today(), created_by_user_id=admin.id, status="closed")
        db.add_all([invoice_sale, reminder_sale, closed])
        db.commit()

        assert invoice_follow_up_status(invoice_sale, as_of=date.today()) == "payment_check_due"
        alerts = invoice_follow_up_alerts(db, as_of=date.today())
        assert {alert["status"] for alert in alerts} >= {"payment_check_due", "reminder_due"}
        with pytest.raises(ValueError, match="Business date is closed"):
            assert_business_date_open(db, date.today())
        with pytest.raises(ValueError, match="Only Admin or Manager"):
            require_closing_manager(None)
        with pytest.raises(ValueError, match="Active Admin or Manager"):
            require_closing_manager(inactive_manager)
        with pytest.raises(ValueError, match="Active user"):
            require_daily_closing_creator(None)
        assert require_daily_closing_creator(admin).id == admin.id


def test_invoice_requirement_and_work_order_sale_validation_edges():
    with SessionLocal() as db:
        admin = _user(db, "Admin", "admin")
        customer = Customer(name="Invoice Customer")
        work_order = Job(title="Empty work order", document_type="work_order", customer=customer)
        db.add_all([customer, work_order])
        db.commit()

        assert _customer_snapshot_for_invoice(None) is None
        assert _customer_snapshot_for_invoice(work_order) is not None
        with pytest.raises(ValueError, match="Sale not found"):
            require_invoice_sale(None)
        with pytest.raises(ValueError, match="Work Order not found"):
            create_sale_from_work_order(db, work_order_id=999, payments=[PaymentInput("cash")], created_by_user_id=admin.id)
        with pytest.raises(ValueError, match="no billable rows"):
            create_sale_from_work_order(db, work_order_id=work_order.id, payments=[PaymentInput("cash")], created_by_user_id=admin.id)
        with pytest.raises(ValueError, match="Invalid sale source"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24")],
                payments=[PaymentInput("cash")],
                source_type="legacy",
                created_by_user_id=admin.id,
            )
        with pytest.raises(ValueError, match="Customer is required"):
            create_sale_from_lines(
                db,
                lines=[SaleLineInput(description="Line", quantity="1", unit_price="1", vat_percent="24")],
                payments=[PaymentInput("invoice")],
                send_to_invoice=True,
                created_by_user_id=admin.id,
            )


def test_cashier_shift_required_setting_branch():
    with SessionLocal() as db:
        set_app_settings(db, {"require_cashier_shift": "yes"})
        assert cashier_shift_required(db) is True
