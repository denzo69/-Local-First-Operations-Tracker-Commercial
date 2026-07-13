from datetime import date, timedelta
from decimal import Decimal

from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.main import app
from app.models import CashRegister, InventoryTransaction, Payment, Product, Role, Setting, Supplier, User
from app.services.inventory_service import (
    add_goods_receipt_line,
    create_default_warehouse,
    create_goods_receipt,
    post_goods_receipt,
)
from app.services.sales_service import close_shift, create_sale_with_payment, ensure_default_roles, open_shift, seller_report


def create_user(db, name="Optional Shift Seller", role_code="seller"):
    ensure_default_roles(db)
    role = db.query(Role).filter(Role.code == role_code).one()
    user = User(
        name=name,
        login_name=name.lower().replace(" ", "."),
        role=role,
        is_active=True,
        can_receive_sales_credit=role_code == "seller",
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def create_register(db, name="Optional Shift Register"):
    register = CashRegister(name=name, is_active=True)
    db.add(register)
    db.commit()
    db.refresh(register)
    return register


def create_stock_product_with_quantity(db, *, user, quantity="2", unit_cost="5"):
    supplier = Supplier(name="Optional Shift Supplier", is_active=True)
    product = Product(name="Optional Shift Stock Product", is_stock_item=True, is_active=True, vat_percent=Decimal("24"))
    db.add_all([supplier, product])
    db.commit()
    db.refresh(supplier)
    db.refresh(product)
    _, location = create_default_warehouse(db)
    receipt = create_goods_receipt(
        db,
        supplier_id=supplier.id,
        receipt_date=date.today(),
        received_by_user_id=user.id,
    )
    add_goods_receipt_line(
        db,
        goods_receipt_id=receipt.id,
        product_id=product.id,
        destination_location_id=location.id,
        quantity_value=quantity,
        purchase_unit_price_ex_vat=unit_cost,
        vat_rate="24",
    )
    post_goods_receipt(db, goods_receipt_id=receipt.id, posted_by_user_id=user.id)
    db.refresh(product)
    return product


def test_cash_and_card_sales_without_shift_succeed():
    with SessionLocal() as db:
        seller = create_user(db)

        cash_sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            payment_method="cash",
            description="Shiftless cash sale",
            quantity="1",
            unit_price="20",
            vat_percent="24",
            created_by_user_id=seller.id,
        )
        card_sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            payment_method="card",
            description="Shiftless card sale",
            quantity="1",
            unit_price="30",
            vat_percent="24",
            created_by_user_id=seller.id,
        )

        assert cash_sale.shift_id is None
        assert card_sale.shift_id is None
        assert cash_sale.payments[0].shift_id is None
        assert card_sale.payments[0].shift_id is None
        assert cash_sale.total == Decimal("20.00")
        assert card_sale.total == Decimal("30.00")


def test_sale_with_shift_still_links_to_shift_and_cash_register():
    with SessionLocal() as db:
        seller = create_user(db)
        register = create_register(db)
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="10",
        )

        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            shift_id=shift.id,
            payment_method="cash",
            description="Linked shift sale",
            quantity="1",
            unit_price="15",
            vat_percent="24",
            created_by_user_id=seller.id,
        )

        assert sale.shift_id == shift.id
        assert sale.cash_register_id == register.id
        assert sale.payments[0].shift_id == shift.id


def test_shift_closing_ignores_shiftless_sales():
    with SessionLocal() as db:
        seller = create_user(db)
        register = create_register(db)
        shift = open_shift(
            db,
            seller_id=seller.id,
            cash_register_id=register.id,
            business_date=date.today(),
            starting_cash="10",
        )
        create_sale_with_payment(
            db,
            seller_id=seller.id,
            payment_method="cash",
            description="Shiftless sale outside drawer",
            quantity="1",
            unit_price="99",
            vat_percent="24",
            created_by_user_id=seller.id,
        )

        closed = close_shift(db, shift_id=shift.id, counted_cash="10")

        assert closed.expected_closing_cash == Decimal("10.00")
        assert closed.cash_over_short == Decimal("0.00")


def test_seller_defaults_to_logged_in_operator_when_not_selected():
    with SessionLocal() as db:
        seller = create_user(db)

        sale = create_sale_with_payment(
            db,
            payment_method="cash",
            description="Default seller sale",
            quantity="1",
            unit_price="12",
            vat_percent="24",
            created_by_user_id=seller.id,
        )

        assert sale.seller_id == seller.id
        assert sale.sold_by_user_id == seller.id
        assert sale.created_by_user_id == seller.id
        assert sale.payments[0].seller_id == seller.id


def test_seller_may_be_empty_for_non_stock_sale():
    with SessionLocal() as db:
        sale = create_sale_with_payment(
            db,
            payment_method="card",
            description="Anonymous seller sale",
            quantity="1",
            unit_price="8",
            vat_percent="24",
        )

        payment = db.query(Payment).filter(Payment.sale_id == sale.id).one()
        assert sale.seller_id is None
        assert sale.sold_by_user_id is None
        assert sale.shift_id is None
        assert payment.seller_id is None
        assert payment.shift_id is None


def test_reports_and_seller_reports_include_shiftless_sales():
    with SessionLocal() as db:
        seller = create_user(db)
        create_sale_with_payment(
            db,
            seller_id=seller.id,
            payment_method="cash",
            description="Shiftless report sale",
            quantity="1",
            unit_price="42",
            vat_percent="24",
            created_by_user_id=seller.id,
        )
        report = seller_report(db, seller_id=seller.id, start_date=date.today(), end_date=date.today() + timedelta(days=1))
        assert report["gross_sales"] == Decimal("42.00")

    with TestClient(app) as client:
        response = client.get(f"/reports?day={date.today().isoformat()}&month={date.today().strftime('%Y-%m')}")

    assert response.status_code == 200
    assert "Shiftless report sale" in response.text
    assert "42.00" in response.text


def test_inventory_updates_without_shift():
    with SessionLocal() as db:
        seller = create_user(db)
        manager = create_user(db, "Optional Shift Manager", "manager")
        product = create_stock_product_with_quantity(db, user=manager, quantity="2", unit_cost="5")

        sale = create_sale_with_payment(
            db,
            seller_id=seller.id,
            payment_method="cash",
            description="Shiftless stock sale",
            quantity="1",
            unit_price="15",
            vat_percent="24",
            product_id=product.id,
            created_by_user_id=seller.id,
        )

        db.refresh(product)
        transaction = (
            db.query(InventoryTransaction)
            .filter(InventoryTransaction.sale_id == sale.id, InventoryTransaction.transaction_type == "sale")
            .one()
        )
        assert sale.shift_id is None
        assert product.current_inventory_quantity == Decimal("1.000")
        assert transaction.created_by_user_id == seller.id


def test_require_cashier_shift_setting_can_restore_old_rule():
    with SessionLocal() as db:
        seller = create_user(db)
        db.add(Setting(key="require_cashier_shift", value="true"))
        db.commit()

        try:
            create_sale_with_payment(
                db,
                seller_id=seller.id,
                payment_method="cash",
                description="Blocked by required shift",
                quantity="1",
                unit_price="10",
                vat_percent="24",
                created_by_user_id=seller.id,
            )
        except ValueError as exc:
            assert "cashier shift is required" in str(exc)
        else:
            raise AssertionError("Sale without shift should be blocked when require_cashier_shift is enabled.")
