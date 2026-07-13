from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import CashRegister, Product, Role, Setting, User
from app.services.sales_service import PaymentInput, SaleLineInput, create_sale_from_lines, open_shift


def _role(db, code: str) -> Role:
    existing = db.query(Role).filter(Role.code == code).first()
    if existing:
        return existing
    created = Role(code=code, name=code.title())
    db.add(created)
    db.commit()
    return created


def _user(db, name: str, role_code: str = "seller", *, credit: bool = True) -> User:
    created = User(
        name=name,
        login_name=f"{name.lower().replace(' ', '.')}.{role_code}.optional",
        role=_role(db, role_code),
        is_active=True,
        can_receive_sales_credit=credit,
    )
    db.add(created)
    db.commit()
    return created


def _service_product(db, name: str, price: str = "12.00") -> Product:
    product = Product(
        name=name,
        unit_price=Decimal(price),
        vat_percent=Decimal("24"),
        is_active=True,
        is_stock_item=False,
    )
    db.add(product)
    db.commit()
    return product


def _set_require_cashier_shift(db, value: bool) -> None:
    setting = db.query(Setting).filter(Setting.key == "require_cashier_shift").first()
    if setting is None:
        db.add(Setting(key="require_cashier_shift", value="true" if value else "false"))
    else:
        setting.value = "true" if value else "false"
    db.commit()


def test_optional_shift_quick_sale_ui_is_not_blocking():
    with TestClient(app) as client:
        response = client.get("/sales/quick")

    assert response.status_code == 200
    assert "Cashier shift (optional)" in response.text
    assert "No cashier shift" in response.text
    assert "No seller on receipt" in response.text


def test_shiftless_sale_succeeds_by_default_but_can_be_required_by_setting():
    with SessionLocal() as db:
        seller = _user(db, "Optional Rule Seller")
        product = _service_product(db, "Optional Rule Service")

        sale = create_sale_from_lines(
            db,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[
                SaleLineInput(
                    product_id=product.id,
                    description="Shiftless default",
                    quantity="1",
                    unit_price="12",
                    vat_percent="24",
                )
            ],
            payments=[PaymentInput("card")],
            idempotency_key="optional-rule-default",
        )
        observed = {"shift_id": sale.shift_id, "business_date": sale.business_date}

        _set_require_cashier_shift(db, True)
        with pytest.raises(ValueError, match="cashier shift is required"):
            create_sale_from_lines(
                db,
                seller_id=seller.id,
                created_by_user_id=seller.id,
                lines=[
                    SaleLineInput(
                        product_id=product.id,
                        description="Shift required",
                        quantity="1",
                        unit_price="12",
                        vat_percent="24",
                    )
                ],
                payments=[PaymentInput("card")],
                idempotency_key="optional-rule-required",
            )

    assert observed["shift_id"] is None
    assert observed["business_date"] == date.today()


def test_selected_shift_still_controls_shift_and_cash_register():
    with SessionLocal() as db:
        seller = _user(db, "Optional Shift Seller")
        product = _service_product(db, "Optional Shift Service")
        register = db.query(CashRegister).first()
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date(2026, 7, 12),
            starting_cash="0",
        )

        sale = create_sale_from_lines(
            db,
            shift_id=shift.id,
            seller_id=seller.id,
            created_by_user_id=seller.id,
            lines=[
                SaleLineInput(
                    product_id=product.id,
                    description="Shift linked",
                    quantity="1",
                    unit_price="12",
                    vat_percent="24",
                )
            ],
            payments=[PaymentInput("cash")],
            idempotency_key="optional-rule-selected-shift",
        )
        observed = {
            "sale_shift_id": sale.shift_id,
            "shift_id": shift.id,
            "sale_cash_register_id": sale.cash_register_id,
            "register_id": register.id,
            "business_date": sale.business_date,
        }

    assert observed["sale_shift_id"] == observed["shift_id"]
    assert observed["sale_cash_register_id"] == observed["register_id"]
    assert observed["business_date"] == date(2026, 7, 12)
