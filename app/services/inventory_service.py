from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryBalance,
    InventoryTransaction,
    Product,
    Supplier,
    User,
    Warehouse,
    WarehouseLocation,
    utc_now,
)
from app.services.audit_service import log_audit_event
from app.services.money_service import money, parse_decimal


MONEY_QUANT = Decimal("0.01")
COST_QUANT = Decimal("0.000001")
QTY_QUANT = Decimal("0.001")
ALLOCATION_METHODS = {"by_value", "by_quantity"}
INVENTORY_MANAGER_ROLES = {"admin", "manager"}
INVENTORY_OPERATIONAL_ROLES = {"admin", "manager", "seller"}
QTY_TOLERANCE = Decimal("0.001")
MONEY_TOLERANCE = Decimal("0.01")
COST_TOLERANCE = Decimal("0.000001")


def cost(value: Decimal) -> Decimal:
    return value.quantize(COST_QUANT, rounding=ROUND_HALF_UP)


def quantity(value: Decimal) -> Decimal:
    return value.quantize(QTY_QUANT, rounding=ROUND_HALF_UP)


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


def require_vat_rate(value, field_name: str = "VAT rate") -> Decimal:
    parsed = parse_finite_decimal(value, field_name, "0")
    if parsed < 0 or parsed > 100:
        raise ValueError(f"{field_name} must be between 0 and 100.")
    return parsed


def vat_amount_from_ex_vat(ex_vat: Decimal, vat_rate: Decimal) -> Decimal:
    return money(ex_vat * vat_rate / Decimal("100"))


def require_positive_quantity(value, field_name: str = "Quantity") -> Decimal:
    parsed = quantity(parse_finite_decimal(value, field_name))
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive.")
    return parsed


def require_inventory_manager(user: User | None) -> User:
    if user is None or not user.is_active:
        raise ValueError("Active Admin or Manager is required.")
    if not user.role or user.role.code not in INVENTORY_MANAGER_ROLES:
        raise ValueError("Only Admin or Manager can manage inventory costing.")
    return user


def require_inventory_operational_user(user: User | None) -> User:
    if user is None or not user.is_active:
        raise ValueError("Active user is required.")
    if not user.role or user.role.code not in INVENTORY_OPERATIONAL_ROLES:
        raise ValueError("Only Admin, Manager, or Seller can manage inventory operations.")
    return user


def create_default_warehouse(db: Session) -> tuple[Warehouse, WarehouseLocation]:
    warehouse = db.query(Warehouse).filter(Warehouse.code == "MAIN").first()
    if warehouse is None:
        warehouse = Warehouse(name="Main warehouse", code="MAIN", is_active=True)
        db.add(warehouse)
        db.flush()
    location = (
        db.query(WarehouseLocation)
        .filter(WarehouseLocation.warehouse_id == warehouse.id, WarehouseLocation.code == "DEFAULT")
        .first()
    )
    if location is None:
        location = WarehouseLocation(
            warehouse_id=warehouse.id,
            code="DEFAULT",
            name="Default location",
            location_type="bin",
            is_active=True,
        )
        db.add(location)
        db.flush()
    return warehouse, location


def create_goods_receipt(
    db: Session,
    *,
    supplier_id: int,
    receipt_date: date,
    received_by_user_id: int,
    delivery_number: str = "",
    invoice_number: str = "",
    freight_total_ex_vat="0",
    freight_vat_rate="0",
    other_costs_total_ex_vat="0",
    other_costs_vat_rate="0",
    allocation_method: str = "by_value",
    notes: str = "",
) -> GoodsReceipt:
    creator = require_inventory_operational_user(db.get(User, received_by_user_id))
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or not supplier.is_active:
        raise ValueError("Active supplier is required.")
    if allocation_method not in ALLOCATION_METHODS:
        raise ValueError("Invalid landed cost allocation method.")
    freight_ex = require_non_negative_money(freight_total_ex_vat, "Freight total ex VAT")
    freight_vat = require_vat_rate(freight_vat_rate, "Freight VAT rate")
    freight_vat_amount = vat_amount_from_ex_vat(freight_ex, freight_vat)
    other_ex = require_non_negative_money(other_costs_total_ex_vat, "Other costs total ex VAT")
    other_vat = require_vat_rate(other_costs_vat_rate, "Other costs VAT rate")
    other_vat_amount = vat_amount_from_ex_vat(other_ex, other_vat)
    receipt = GoodsReceipt(
        supplier_id=supplier_id,
        receipt_date=receipt_date,
        delivery_number=delivery_number.strip() or None,
        invoice_number=invoice_number.strip() or None,
        freight_total_ex_vat=freight_ex,
        freight_vat_rate=freight_vat,
        freight_vat_amount=freight_vat_amount,
        freight_total_inc_vat=money(freight_ex + freight_vat_amount),
        other_costs_total_ex_vat=other_ex,
        other_costs_vat_rate=other_vat,
        other_costs_vat_amount=other_vat_amount,
        other_costs_total_inc_vat=money(other_ex + other_vat_amount),
        allocation_method=allocation_method,
        received_by_user_id=creator.id,
        notes=notes.strip() or None,
        status="draft",
    )
    db.add(receipt)
    db.flush()
    log_audit_event(
        db,
        event_type="goods_receipt.created",
        entity_type="goods_receipt",
        entity_id=receipt.id,
        description=f"Goods receipt created for supplier {supplier.name}.",
    )
    db.commit()
    db.refresh(receipt)
    return receipt


def add_goods_receipt_line(
    db: Session,
    *,
    goods_receipt_id: int,
    product_id: int,
    destination_location_id: int,
    quantity_value,
    purchase_unit_price_ex_vat,
    vat_rate="24",
) -> GoodsReceiptLine:
    receipt = db.get(GoodsReceipt, goods_receipt_id)
    if receipt is None:
        raise ValueError("Goods receipt not found.")
    if receipt.status != "draft":
        raise ValueError("Posted or cancelled goods receipts cannot be edited.")
    product = db.get(Product, product_id)
    if product is None or not product.is_active:
        raise ValueError("Active product is required.")
    if not product.is_stock_item:
        raise ValueError("Only stock products can be received into inventory.")
    location = db.get(WarehouseLocation, destination_location_id)
    if location is None or not location.is_active:
        raise ValueError("Active warehouse location is required.")
    qty = require_positive_quantity(quantity_value)
    unit_ex = require_non_negative_money(purchase_unit_price_ex_vat, "Purchase unit price ex VAT")
    vat = parse_finite_decimal(vat_rate, "VAT rate", "24")
    if vat < 0:
        raise ValueError("VAT rate cannot be negative.")
    line_total_ex = money(qty * unit_ex)
    unit_inc = money(unit_ex * (Decimal("1") + vat / Decimal("100")))
    line = GoodsReceiptLine(
        goods_receipt_id=goods_receipt_id,
        product_id=product_id,
        destination_location_id=destination_location_id,
        quantity=qty,
        purchase_unit_price_ex_vat=unit_ex,
        vat_rate=vat,
        purchase_unit_price_inc_vat=unit_inc,
        line_total_ex_vat=line_total_ex,
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return line


def allocate_landed_costs(lines: list[GoodsReceiptLine], freight: Decimal, other: Decimal, method: str) -> dict[int, dict[str, Decimal]]:
    if not lines:
        raise ValueError("Goods receipt requires at least one line.")
    if method not in ALLOCATION_METHODS:
        raise ValueError("Invalid landed cost allocation method.")

    if method == "by_quantity":
        weights = [parse_decimal(line.quantity) for line in lines]
    else:
        weights = [money(parse_decimal(line.quantity) * parse_decimal(line.purchase_unit_price_ex_vat)) for line in lines]
        if sum(weights, Decimal("0")) == 0:
            weights = [parse_decimal(line.quantity) for line in lines]

    total_weight = sum(weights, Decimal("0"))
    if total_weight <= 0:
        raise ValueError("Cannot allocate landed costs without positive line weight.")

    def split(total: Decimal) -> list[Decimal]:
        allocated: list[Decimal] = []
        running = Decimal("0")
        last_index = len(weights) - 1
        for index, weight in enumerate(weights):
            if index == last_index:
                share = money(total - running)
            else:
                share = money(total * weight / total_weight)
                running += share
            allocated.append(share)
        return allocated

    freight_allocations = split(freight)
    other_allocations = split(other)
    return {
        line.id: {
            "freight": freight_allocations[index],
            "other": other_allocations[index],
        }
        for index, line in enumerate(lines)
    }


def preview_goods_receipt(db: Session, receipt: GoodsReceipt) -> dict:
    lines = sorted(receipt.lines, key=lambda row: row.id or 0)
    freight = require_non_negative_money(receipt.freight_total_ex_vat, "Freight total ex VAT")
    other = require_non_negative_money(receipt.other_costs_total_ex_vat, "Other costs total ex VAT")
    allocations = allocate_landed_costs(lines, freight, other, receipt.allocation_method or "by_value")

    line_previews = []
    purchase_total = Decimal("0")
    product_vat_total = Decimal("0")
    landed_total = Decimal("0")
    projected_by_product: dict[int, dict[str, Decimal | None]] = {}
    running_by_product: dict[int, dict[str, Decimal | None]] = {}
    for line in lines:
        if line.product_id not in running_by_product:
            old_qty = quantity(parse_decimal(line.product.current_inventory_quantity or 0))
            old_avg = cost(parse_decimal(line.product.current_weighted_average_cost_ex_vat or 0))
            old_value = money(
                parse_decimal(
                    line.product.current_inventory_value_ex_vat
                    if line.product.current_inventory_value_ex_vat is not None
                    else old_qty * old_avg
                )
            )
            if old_qty < 0:
                raise ValueError("Negative stock must be corrected before posting receipts.")
            running_by_product[line.product_id] = {
                "quantity": old_qty,
                "value": old_value,
                "average": old_avg if old_qty > 0 else None,
            }

        product_state = running_by_product[line.product_id]
        qty = parse_decimal(line.quantity)
        line_purchase = money(qty * parse_decimal(line.purchase_unit_price_ex_vat))
        allocated_freight = allocations[line.id]["freight"]
        allocated_other = allocations[line.id]["other"]
        landed_line_total = money(line_purchase + allocated_freight + allocated_other)
        landed_unit = cost(landed_line_total / qty)
        old_qty = quantity(parse_decimal(product_state["quantity"] or 0))
        old_avg = cost(parse_decimal(product_state["average"] or 0))
        old_value = money(parse_decimal(product_state["value"] or 0))
        new_qty = quantity(old_qty + qty)
        new_value = money(old_value + landed_line_total)
        new_avg = cost(new_value / new_qty)
        product_state["quantity"] = new_qty
        product_state["value"] = new_value
        product_state["average"] = new_avg
        projected_by_product[line.product_id] = {
            "quantity": new_qty,
            "value": new_value,
            "average": new_avg,
        }
        vat_amount = money(line_purchase * parse_decimal(line.vat_rate) / Decimal("100"))
        purchase_total += line_purchase
        product_vat_total += vat_amount
        landed_total += landed_line_total
        line_previews.append(
            {
                "line": line,
                "purchase_value_ex_vat": money(line_purchase),
                "allocated_freight_ex_vat": allocated_freight,
                "allocated_other_costs_ex_vat": allocated_other,
                "landed_unit_cost_ex_vat": landed_unit,
                "purchase_unit_price_inc_vat": money(line.purchase_unit_price_inc_vat),
                "current_weighted_average_cost": cost(old_avg),
                "projected_weighted_average_cost": new_avg,
                "projected_inventory_quantity": new_qty,
                "projected_inventory_value_ex_vat": new_value,
                "vat_amount": vat_amount,
                "calculation": {
                    "old_quantity": old_qty,
                    "old_average_cost": cost(old_avg),
                    "old_value": old_value,
                    "new_quantity": qty,
                    "new_landed_unit_cost": landed_unit,
                    "new_value": landed_line_total,
                    "combined_quantity": new_qty,
                    "combined_value": new_value,
                    "new_average_cost": new_avg,
                },
            }
        )

    freight_vat_rate = require_vat_rate(receipt.freight_vat_rate or 0, "Freight VAT rate")
    freight_vat_amount = vat_amount_from_ex_vat(freight, freight_vat_rate)
    other_vat_rate = require_vat_rate(receipt.other_costs_vat_rate or 0, "Other costs VAT rate")
    other_vat_amount = vat_amount_from_ex_vat(other, other_vat_rate)
    vat_total = money(product_vat_total + freight_vat_amount + other_vat_amount)
    return {
        "receipt": receipt,
        "lines": line_previews,
        "purchase_total_ex_vat": money(purchase_total),
        "freight_total_ex_vat": freight,
        "freight_vat_rate": freight_vat_rate,
        "freight_vat_amount": freight_vat_amount,
        "freight_total_inc_vat": money(freight + freight_vat_amount),
        "other_costs_total_ex_vat": other,
        "other_costs_vat_rate": other_vat_rate,
        "other_costs_vat_amount": other_vat_amount,
        "other_costs_total_inc_vat": money(other + other_vat_amount),
        "total_landed_cost_ex_vat": money(landed_total),
        "product_vat_total": money(product_vat_total),
        "vat_total": money(vat_total),
        "total_inc_vat": money(purchase_total + freight + other + vat_total),
        "allocation_method": receipt.allocation_method or "by_value",
        "projected_by_product": projected_by_product,
    }


def _get_or_create_balance(db: Session, *, product_id: int, location_id: int) -> InventoryBalance:
    balance = (
        db.query(InventoryBalance)
        .filter(
            InventoryBalance.product_id == product_id,
            InventoryBalance.warehouse_location_id == location_id,
        )
        .first()
    )
    if balance is None:
        balance = InventoryBalance(
            product_id=product_id,
            warehouse_location_id=location_id,
            quantity_on_hand=Decimal("0.000"),
            quantity_reserved=Decimal("0.000"),
            quantity_available=Decimal("0.000"),
            inventory_value_ex_vat=Decimal("0.00"),
        )
        db.add(balance)
        db.flush()
    return balance


def _location(db: Session, location_id: int) -> WarehouseLocation:
    location = db.get(WarehouseLocation, location_id)
    if location is None or not location.is_active:
        raise ValueError("Active warehouse location is required.")
    return location


def _ledger_state_from_transactions(db: Session, product_id: int) -> tuple[Decimal, Decimal, Decimal | None]:
    transactions = (
        db.query(InventoryTransaction)
        .filter(InventoryTransaction.product_id == product_id)
        .order_by(InventoryTransaction.created_at.asc(), InventoryTransaction.id.asc())
        .all()
    )
    stock = sum((parse_decimal(row.quantity_change) for row in transactions), Decimal("0"))
    value = sum((parse_decimal(row.total_inventory_cost) for row in transactions), Decimal("0"))
    avg = cost(value / stock) if stock > 0 else None
    return quantity(stock), money(value), avg


def _within_tolerance(actual: Decimal | None, expected: Decimal | None, tolerance: Decimal) -> bool:
    actual_value = Decimal("0") if actual is None else parse_decimal(actual)
    expected_value = Decimal("0") if expected is None else parse_decimal(expected)
    return abs(actual_value - expected_value) <= tolerance


def inventory_reconciliation(db: Session, *, product_ids: set[int] | None = None) -> dict:
    product_query = db.query(Product).filter(Product.is_stock_item.is_(True))
    if product_ids:
        product_query = product_query.filter(Product.id.in_(product_ids))
    products = product_query.order_by(Product.id.asc()).all()
    product_mismatches: list[dict] = []
    location_mismatches: list[dict] = []

    for product in products:
        expected_qty, expected_value, expected_avg = _ledger_state_from_transactions(db, product.id)
        cached_qty = quantity(parse_decimal(product.current_inventory_quantity or 0))
        cached_value = money(parse_decimal(product.current_inventory_value_ex_vat or 0))
        cached_avg = (
            cost(parse_decimal(product.current_weighted_average_cost_ex_vat))
            if product.current_weighted_average_cost_ex_vat is not None
            else None
        )
        if (
            not _within_tolerance(cached_qty, expected_qty, QTY_TOLERANCE)
            or not _within_tolerance(cached_value, expected_value, MONEY_TOLERANCE)
            or not _within_tolerance(cached_avg, expected_avg, COST_TOLERANCE)
        ):
            product_mismatches.append(
                {
                    "product": product,
                    "cached_quantity": cached_qty,
                    "expected_quantity": expected_qty,
                    "cached_value": cached_value,
                    "expected_value": expected_value,
                    "cached_average": cached_avg,
                    "expected_average": expected_avg,
                }
            )

        balances = db.query(InventoryBalance).filter(InventoryBalance.product_id == product.id).all()
        location_ids = {balance.warehouse_location_id for balance in balances}
        transaction_location_ids = {
            row[0]
            for row in db.query(InventoryTransaction.shelf_location_id)
            .filter(InventoryTransaction.product_id == product.id, InventoryTransaction.shelf_location_id.is_not(None))
            .distinct()
            .all()
        }
        for location_id in sorted(location_ids | transaction_location_ids):
            balance = next((row for row in balances if row.warehouse_location_id == location_id), None)
            transactions = (
                db.query(InventoryTransaction)
                .filter(InventoryTransaction.product_id == product.id, InventoryTransaction.shelf_location_id == location_id)
                .all()
            )
            expected_location_qty = quantity(
                sum((parse_decimal(row.quantity_change or 0) for row in transactions), Decimal("0"))
            )
            expected_location_value = money(
                sum((parse_decimal(row.total_inventory_cost or 0) for row in transactions), Decimal("0"))
            )
            expected_location_avg = (
                cost(expected_location_value / expected_location_qty)
                if expected_location_qty > 0
                else None
            )
            cached_location_qty = quantity(parse_decimal(balance.quantity_on_hand or 0)) if balance else Decimal("0.000")
            cached_location_value = money(parse_decimal(balance.inventory_value_ex_vat or 0)) if balance else Decimal("0.00")
            cached_location_avg = (
                cost(parse_decimal(balance.weighted_average_cost_ex_vat))
                if balance and balance.weighted_average_cost_ex_vat is not None
                else None
            )
            if (
                not _within_tolerance(cached_location_qty, expected_location_qty, QTY_TOLERANCE)
                or not _within_tolerance(cached_location_value, expected_location_value, MONEY_TOLERANCE)
                or not _within_tolerance(cached_location_avg, expected_location_avg, COST_TOLERANCE)
            ):
                location_mismatches.append(
                    {
                        "product": product,
                        "location_id": location_id,
                        "balance": balance,
                        "cached_quantity": cached_location_qty,
                        "expected_quantity": expected_location_qty,
                        "cached_value": cached_location_value,
                        "expected_value": expected_location_value,
                        "cached_average": cached_location_avg,
                        "expected_average": expected_location_avg,
                    }
                )

    return {
        "product_mismatches": product_mismatches,
        "location_mismatches": location_mismatches,
        "is_clean": not product_mismatches and not location_mismatches,
        "tolerances": {
            "quantity": QTY_TOLERANCE,
            "money": MONEY_TOLERANCE,
            "cost": COST_TOLERANCE,
        },
    }


def assert_inventory_cache_consistent(db: Session, *, product_ids: set[int]) -> None:
    report = inventory_reconciliation(db, product_ids=product_ids)
    if not report["is_clean"]:
        raise ValueError("Inventory cache differs from ledger. Run reconciliation repair before posting new stock changes.")


def repair_inventory_caches_from_ledger(db: Session, *, user_id: int, reason: str) -> dict:
    user = require_inventory_manager(db.get(User, user_id))
    repair_reason = reason.strip()
    if not repair_reason:
        raise ValueError("Repair reason is required.")
    before = inventory_reconciliation(db)
    touched_products = {item["product"].id for item in before["product_mismatches"]}
    touched_products.update(item["product"].id for item in before["location_mismatches"])
    for product_id in touched_products:
        product = db.get(Product, product_id)
        if product is None:
            continue
        expected_qty, expected_value, expected_avg = _ledger_state_from_transactions(db, product.id)
        _sync_product_cache(product, stock=expected_qty, value=expected_value, average_cost=expected_avg)
        location_ids = {
            row[0]
            for row in db.query(InventoryTransaction.shelf_location_id)
            .filter(InventoryTransaction.product_id == product.id, InventoryTransaction.shelf_location_id.is_not(None))
            .distinct()
            .all()
        }
        for location_id in location_ids:
            balance = _get_or_create_balance(db, product_id=product.id, location_id=location_id)
            transactions = (
                db.query(InventoryTransaction)
                .filter(InventoryTransaction.product_id == product.id, InventoryTransaction.shelf_location_id == location_id)
                .all()
            )
            location_qty = quantity(sum((parse_decimal(row.quantity_change or 0) for row in transactions), Decimal("0")))
            location_value = money(sum((parse_decimal(row.total_inventory_cost or 0) for row in transactions), Decimal("0")))
            balance.quantity_on_hand = location_qty
            balance.quantity_available = quantity(location_qty - parse_decimal(balance.quantity_reserved or 0))
            balance.inventory_value_ex_vat = location_value
            balance.weighted_average_cost_ex_vat = cost(location_value / location_qty) if location_qty > 0 else None
            balance.updated_at = utc_now()
    log_audit_event(
        db,
        event_type="inventory.cache_repaired",
        entity_type="inventory",
        entity_id=None,
        description=f"Inventory caches repaired from ledger by {user.id}/{user.name}: {repair_reason}.",
    )
    db.commit()
    return before


def _create_transaction(
    db: Session,
    *,
    product: Product,
    location: WarehouseLocation,
    transaction_type: str,
    quantity_change: Decimal,
    unit_cost_ex_vat: Decimal,
    total_inventory_cost: Decimal,
    created_by_user_id: int,
    inventory_value_before: Decimal,
    inventory_value_after: Decimal,
    stock_before: Decimal,
    stock_after: Decimal,
    weighted_average_cost_before: Decimal | None,
    weighted_average_cost_after: Decimal | None,
    allocated_freight_cost: Decimal = Decimal("0.00"),
    allocated_other_cost: Decimal = Decimal("0.00"),
    supplier_id: int | None = None,
    purchase_invoice_number: str | None = None,
    delivery_note_number: str | None = None,
    goods_receipt_id: int | None = None,
    work_order_id: int | None = None,
    sale_id: int | None = None,
    adjustment_reason: str | None = None,
    reference: str | None = None,
    reversal_of_transaction_id: int | None = None,
) -> InventoryTransaction:
    transaction = InventoryTransaction(
        product_id=product.id,
        warehouse_id=location.warehouse_id,
        shelf_location_id=location.id,
        transaction_type=transaction_type,
        quantity_change=quantity(quantity_change),
        unit_cost_ex_vat=cost(unit_cost_ex_vat),
        allocated_freight_cost=money(allocated_freight_cost),
        allocated_other_cost=money(allocated_other_cost),
        total_inventory_cost=money(total_inventory_cost),
        inventory_value_before=money(inventory_value_before),
        inventory_value_after=money(inventory_value_after),
        stock_before=quantity(stock_before),
        stock_after=quantity(stock_after),
        weighted_average_cost_before=cost(weighted_average_cost_before) if weighted_average_cost_before is not None else None,
        weighted_average_cost_after=cost(weighted_average_cost_after) if weighted_average_cost_after is not None else None,
        supplier_id=supplier_id,
        purchase_invoice_number=purchase_invoice_number,
        delivery_note_number=delivery_note_number,
        goods_receipt_id=goods_receipt_id,
        work_order_id=work_order_id,
        sale_id=sale_id,
        adjustment_reason=adjustment_reason,
        reference=reference,
        created_by_user_id=created_by_user_id,
        reversal_of_transaction_id=reversal_of_transaction_id,
    )
    db.add(transaction)
    return transaction


def _sync_product_cache(product: Product, *, stock: Decimal, value: Decimal, average_cost: Decimal | None) -> None:
    product.current_inventory_quantity = quantity(stock)
    product.current_inventory_value_ex_vat = money(value)
    product.current_weighted_average_cost_ex_vat = cost(average_cost) if average_cost is not None else None
    product.updated_at = utc_now()


def post_goods_receipt(db: Session, *, goods_receipt_id: int, posted_by_user_id: int) -> GoodsReceipt:
    user = require_inventory_operational_user(db.get(User, posted_by_user_id))
    receipt = db.get(GoodsReceipt, goods_receipt_id)
    if receipt is None:
        raise ValueError("Goods receipt not found.")
    if receipt.status != "draft":
        raise ValueError("Only draft goods receipts can be posted.")
    preview = preview_goods_receipt(db, receipt)
    assert_inventory_cache_consistent(db, product_ids=set(preview["projected_by_product"].keys()))

    for item in preview["lines"]:
        line = item["line"]
        product = line.product
        location = _location(db, line.destination_location_id)
        balance = _get_or_create_balance(db, product_id=line.product_id, location_id=line.destination_location_id)
        old_product_qty = quantity(parse_decimal(item["calculation"]["old_quantity"]))
        old_product_avg = cost(parse_decimal(item["calculation"]["old_average_cost"]))
        old_product_value = money(parse_decimal(item["calculation"]["old_value"]))
        received_qty = quantity(parse_decimal(line.quantity))
        landed_line_total = money(item["purchase_value_ex_vat"] + item["allocated_freight_ex_vat"] + item["allocated_other_costs_ex_vat"])
        new_product_qty = quantity(parse_decimal(item["calculation"]["combined_quantity"]))
        if new_product_qty <= 0:
            raise ValueError("Posting receipt would not create positive inventory quantity.")
        new_product_value = money(parse_decimal(item["calculation"]["combined_value"]))
        new_product_avg = cost(parse_decimal(item["calculation"]["new_average_cost"]))

        line.allocated_freight_ex_vat = item["allocated_freight_ex_vat"]
        line.allocated_other_costs_ex_vat = item["allocated_other_costs_ex_vat"]
        line.landed_unit_cost_ex_vat = item["landed_unit_cost_ex_vat"]
        line.line_total_ex_vat = item["purchase_value_ex_vat"]
        line.purchase_unit_price_inc_vat = item["purchase_unit_price_inc_vat"]

        old_balance_qty = quantity(parse_decimal(balance.quantity_on_hand or 0))
        old_balance_value = money(parse_decimal(balance.inventory_value_ex_vat or 0))
        new_balance_qty = quantity(old_balance_qty + received_qty)
        new_balance_value = money(old_balance_value + landed_line_total)
        balance.quantity_on_hand = new_balance_qty
        balance.quantity_available = quantity(new_balance_qty - parse_decimal(balance.quantity_reserved or 0))
        balance.inventory_value_ex_vat = new_balance_value
        balance.weighted_average_cost_ex_vat = cost(new_balance_value / new_balance_qty)
        balance.updated_at = utc_now()

        product.current_purchase_price_ex_vat = line.purchase_unit_price_ex_vat
        product.current_purchase_price_inc_vat = line.purchase_unit_price_inc_vat

        _create_transaction(
            db,
            product=product,
            location=location,
            transaction_type="purchase",
            quantity_change=received_qty,
            unit_cost_ex_vat=item["landed_unit_cost_ex_vat"],
            allocated_freight_cost=item["allocated_freight_ex_vat"],
            allocated_other_cost=item["allocated_other_costs_ex_vat"],
            total_inventory_cost=landed_line_total,
            inventory_value_before=old_product_value,
            inventory_value_after=new_product_value,
            stock_before=old_product_qty,
            stock_after=new_product_qty,
            weighted_average_cost_before=old_product_avg,
            weighted_average_cost_after=new_product_avg,
            supplier_id=receipt.supplier_id,
            purchase_invoice_number=receipt.invoice_number,
            delivery_note_number=receipt.delivery_number,
            goods_receipt_id=receipt.id,
            reference=receipt.delivery_number,
            created_by_user_id=user.id,
        )

    for product_id, projected in preview["projected_by_product"].items():
        product = db.get(Product, product_id)
        if product is not None:
            _sync_product_cache(
                product,
                stock=parse_decimal(projected["quantity"] or 0),
                value=parse_decimal(projected["value"] or 0),
                average_cost=parse_decimal(projected["average"]) if projected["average"] is not None else None,
            )

    receipt.freight_vat_rate = preview["freight_vat_rate"]
    receipt.freight_vat_amount = preview["freight_vat_amount"]
    receipt.freight_total_inc_vat = preview["freight_total_inc_vat"]
    receipt.other_costs_vat_rate = preview["other_costs_vat_rate"]
    receipt.other_costs_vat_amount = preview["other_costs_vat_amount"]
    receipt.other_costs_total_inc_vat = preview["other_costs_total_inc_vat"]
    receipt.status = "posted"
    receipt.posted_at = utc_now()
    log_audit_event(
        db,
        event_type="goods_receipt.posted",
        entity_type="goods_receipt",
        entity_id=receipt.id,
        description=(
            f"Goods receipt posted; allocation={preview['allocation_method']}; "
            f"landed_total={preview['total_landed_cost_ex_vat']}."
        ),
    )
    db.commit()
    db.refresh(receipt)
    return receipt


def cancel_goods_receipt(db: Session, *, goods_receipt_id: int, user_id: int, reason: str) -> GoodsReceipt:
    user = require_inventory_manager(db.get(User, user_id))
    receipt = db.get(GoodsReceipt, goods_receipt_id)
    if receipt is None:
        raise ValueError("Goods receipt not found.")
    if receipt.status != "posted":
        raise ValueError("Only posted goods receipts can be cancelled.")
    cancellation_reason = reason.strip()
    if not cancellation_reason:
        raise ValueError("Cancellation reason is required.")
    original_transactions = [
        transaction
        for transaction in receipt.transactions
        if transaction.transaction_type == "purchase" and transaction.reversal_of_transaction_id is None
    ]
    assert_inventory_cache_consistent(db, product_ids={transaction.product_id for transaction in original_transactions})
    for transaction in original_transactions:
        product = transaction.product
        location = _location(db, transaction.shelf_location_id)
        balance = _get_or_create_balance(db, product_id=transaction.product_id, location_id=location.id)
        old_qty = quantity(parse_decimal(product.current_inventory_quantity or 0))
        old_avg = cost(parse_decimal(product.current_weighted_average_cost_ex_vat or 0))
        old_value = money(parse_decimal(product.current_inventory_value_ex_vat or 0))
        reverse_qty = quantity(parse_decimal(transaction.quantity_change))
        reverse_value = money(transaction.total_inventory_cost)
        if old_qty - reverse_qty < 0:
            raise ValueError("Cannot cancel receipt because it would create negative stock.")
        new_qty = quantity(old_qty - reverse_qty)
        new_value = money(old_value - reverse_value)
        new_avg = cost(new_value / new_qty) if new_qty > 0 else None

        balance_qty = quantity(parse_decimal(balance.quantity_on_hand or 0))
        balance_value = money(parse_decimal(balance.inventory_value_ex_vat or 0))
        if balance_qty - reverse_qty < 0:
            raise ValueError("Cannot cancel receipt because location stock would become negative.")
        balance.quantity_on_hand = quantity(balance_qty - reverse_qty)
        balance.quantity_available = quantity(parse_decimal(balance.quantity_on_hand) - parse_decimal(balance.quantity_reserved or 0))
        balance.inventory_value_ex_vat = money(balance_value - reverse_value)
        balance.weighted_average_cost_ex_vat = (
            cost(parse_decimal(balance.inventory_value_ex_vat) / parse_decimal(balance.quantity_on_hand))
            if parse_decimal(balance.quantity_on_hand) > 0
            else None
        )
        balance.updated_at = utc_now()
        _sync_product_cache(product, stock=new_qty, value=new_value, average_cost=new_avg)
        _create_transaction(
            db,
            product=product,
            location=location,
            transaction_type="inventory_adjustment",
            quantity_change=quantity(-reverse_qty),
            unit_cost_ex_vat=transaction.unit_cost_ex_vat,
            total_inventory_cost=money(-reverse_value),
            inventory_value_before=old_value,
            inventory_value_after=new_value,
            stock_before=old_qty,
            stock_after=new_qty,
            weighted_average_cost_before=old_avg,
            weighted_average_cost_after=new_avg,
            supplier_id=transaction.supplier_id,
            purchase_invoice_number=transaction.purchase_invoice_number,
            delivery_note_number=transaction.delivery_note_number,
            goods_receipt_id=receipt.id,
            adjustment_reason=cancellation_reason,
            reference=f"Reversal of transaction {transaction.id}",
            created_by_user_id=user.id,
            reversal_of_transaction_id=transaction.id,
        )

    receipt.status = "cancelled"
    receipt.cancelled_at = utc_now()
    receipt.cancellation_reason = cancellation_reason
    log_audit_event(
        db,
        event_type="goods_receipt.cancelled",
        entity_type="goods_receipt",
        entity_id=receipt.id,
        description=f"Goods receipt cancelled: {cancellation_reason}.",
    )
    db.commit()
    db.refresh(receipt)
    return receipt


def issue_stock_for_sale(
    db: Session,
    *,
    product_id: int,
    warehouse_location_id: int,
    quantity_value,
    sale_id: int,
    created_by_user_id: int,
    commit: bool = True,
) -> InventoryTransaction:
    user = require_inventory_operational_user(db.get(User, created_by_user_id))
    product = db.get(Product, product_id)
    if product is None or not product.is_stock_item:
        raise ValueError("Stock product is required.")
    assert_inventory_cache_consistent(db, product_ids={product_id})
    location = _location(db, warehouse_location_id)
    balance = _get_or_create_balance(db, product_id=product_id, location_id=warehouse_location_id)
    qty = require_positive_quantity(quantity_value)
    old_qty = quantity(parse_decimal(product.current_inventory_quantity or 0))
    old_avg = cost(parse_decimal(product.current_weighted_average_cost_ex_vat or 0))
    old_value = money(parse_decimal(product.current_inventory_value_ex_vat or 0))
    if old_qty - qty < 0:
        raise ValueError("Negative stock is not allowed.")
    total_cost = money(qty * old_avg)
    new_qty = quantity(old_qty - qty)
    new_value = money(old_value - total_cost)
    new_avg = old_avg if new_qty > 0 else None

    balance_qty = quantity(parse_decimal(balance.quantity_on_hand or 0))
    if balance_qty - qty < 0:
        raise ValueError("Negative stock is not allowed.")
    balance_value = money(parse_decimal(balance.inventory_value_ex_vat or 0))
    balance.quantity_on_hand = quantity(balance_qty - qty)
    balance.quantity_available = quantity(parse_decimal(balance.quantity_on_hand) - parse_decimal(balance.quantity_reserved or 0))
    balance.inventory_value_ex_vat = money(balance_value - total_cost)
    balance.weighted_average_cost_ex_vat = (
        cost(parse_decimal(balance.inventory_value_ex_vat) / parse_decimal(balance.quantity_on_hand))
        if parse_decimal(balance.quantity_on_hand) > 0
        else None
    )
    _sync_product_cache(product, stock=new_qty, value=new_value, average_cost=new_avg)
    transaction = _create_transaction(
        db,
        product=product,
        location=location,
        transaction_type="sale",
        quantity_change=quantity(-qty),
        unit_cost_ex_vat=old_avg,
        total_inventory_cost=money(-total_cost),
        inventory_value_before=old_value,
        inventory_value_after=new_value,
        stock_before=old_qty,
        stock_after=new_qty,
        weighted_average_cost_before=old_avg,
        weighted_average_cost_after=new_avg,
        sale_id=sale_id,
        created_by_user_id=user.id,
    )
    db.flush()
    log_audit_event(
        db,
        event_type="inventory.sale_issue",
        entity_type="inventory_transaction",
        entity_id=transaction.id,
        description=f"Sale issue recorded for product {product.name}: {qty} at {old_avg}.",
    )
    if commit:
        db.commit()
        db.refresh(transaction)
    return transaction


def issue_stock_for_sale_from_available_locations(
    db: Session,
    *,
    product_id: int,
    quantity_value,
    sale_id: int,
    created_by_user_id: int,
    commit: bool = True,
) -> list[InventoryTransaction]:
    qty_remaining = require_positive_quantity(quantity_value)
    transactions: list[InventoryTransaction] = []
    balances = (
        db.query(InventoryBalance)
        .filter(InventoryBalance.product_id == product_id, InventoryBalance.quantity_on_hand > 0)
        .order_by(InventoryBalance.warehouse_location_id.asc())
        .all()
    )
    total_available = quantity(
        sum((parse_decimal(balance.quantity_on_hand or 0) for balance in balances), Decimal("0"))
    )
    if total_available < qty_remaining:
        raise ValueError("Negative stock is not allowed.")
    for balance in balances:
        if qty_remaining <= 0:
            break
        issue_qty = min(qty_remaining, quantity(parse_decimal(balance.quantity_on_hand)))
        transactions.append(
            issue_stock_for_sale(
                db,
                product_id=product_id,
                warehouse_location_id=balance.warehouse_location_id,
                quantity_value=issue_qty,
                sale_id=sale_id,
                created_by_user_id=created_by_user_id,
                commit=False,
            )
        )
        qty_remaining = quantity(qty_remaining - issue_qty)
    if qty_remaining > 0:
        raise ValueError("Negative stock is not allowed.")
    if commit:
        db.commit()
        for transaction in transactions:
            db.refresh(transaction)
    return transactions


def transfer_stock(
    db: Session,
    *,
    product_id: int,
    from_location_id: int,
    to_location_id: int,
    quantity_value,
    created_by_user_id: int,
) -> list[InventoryTransaction]:
    user = require_inventory_operational_user(db.get(User, created_by_user_id))
    product = db.get(Product, product_id)
    if product is None or not product.is_stock_item:
        raise ValueError("Stock product is required.")
    assert_inventory_cache_consistent(db, product_ids={product_id})
    source_location = _location(db, from_location_id)
    target_location = _location(db, to_location_id)
    source = _get_or_create_balance(db, product_id=product_id, location_id=from_location_id)
    target = _get_or_create_balance(db, product_id=product_id, location_id=to_location_id)
    qty = require_positive_quantity(quantity_value)
    avg = cost(parse_decimal(product.current_weighted_average_cost_ex_vat or 0))
    total_value = money(qty * avg)
    if parse_decimal(source.quantity_on_hand or 0) - qty < 0:
        raise ValueError("Negative stock is not allowed.")

    product_qty = quantity(parse_decimal(product.current_inventory_quantity or 0))
    product_value = money(parse_decimal(product.current_inventory_value_ex_vat or 0))
    source.quantity_on_hand = quantity(parse_decimal(source.quantity_on_hand) - qty)
    source.quantity_available = quantity(parse_decimal(source.quantity_on_hand) - parse_decimal(source.quantity_reserved or 0))
    source.inventory_value_ex_vat = money(parse_decimal(source.inventory_value_ex_vat or 0) - total_value)
    source.weighted_average_cost_ex_vat = (
        cost(parse_decimal(source.inventory_value_ex_vat) / parse_decimal(source.quantity_on_hand))
        if parse_decimal(source.quantity_on_hand) > 0
        else None
    )
    target.quantity_on_hand = quantity(parse_decimal(target.quantity_on_hand or 0) + qty)
    target.quantity_available = quantity(parse_decimal(target.quantity_on_hand) - parse_decimal(target.quantity_reserved or 0))
    target.inventory_value_ex_vat = money(parse_decimal(target.inventory_value_ex_vat or 0) + total_value)
    target.weighted_average_cost_ex_vat = cost(parse_decimal(target.inventory_value_ex_vat) / parse_decimal(target.quantity_on_hand))

    outgoing = _create_transaction(
        db,
        product=product,
        location=source_location,
        transaction_type="shelf_transfer",
        quantity_change=quantity(-qty),
        unit_cost_ex_vat=avg,
        total_inventory_cost=money(-total_value),
        inventory_value_before=product_value,
        inventory_value_after=product_value,
        stock_before=product_qty,
        stock_after=product_qty,
        weighted_average_cost_before=avg,
        weighted_average_cost_after=avg,
        reference=f"Transfer to {target_location.code}",
        created_by_user_id=user.id,
    )
    incoming = _create_transaction(
        db,
        product=product,
        location=target_location,
        transaction_type="shelf_transfer",
        quantity_change=qty,
        unit_cost_ex_vat=avg,
        total_inventory_cost=total_value,
        inventory_value_before=product_value,
        inventory_value_after=product_value,
        stock_before=product_qty,
        stock_after=product_qty,
        weighted_average_cost_before=avg,
        weighted_average_cost_after=avg,
        reference=f"Transfer from {source_location.code}",
        created_by_user_id=user.id,
    )
    db.commit()
    db.refresh(outgoing)
    db.refresh(incoming)
    return [outgoing, incoming]


def inventory_ledger(
    db: Session,
    *,
    product_id: int | None = None,
    warehouse_id: int | None = None,
    supplier_id: int | None = None,
    transaction_type: str | None = None,
    user_id: int | None = None,
    date_from=None,
    date_to=None,
) -> list[InventoryTransaction]:
    query = db.query(InventoryTransaction)
    if product_id:
        query = query.filter(InventoryTransaction.product_id == product_id)
    if warehouse_id:
        query = query.filter(InventoryTransaction.warehouse_id == warehouse_id)
    if supplier_id:
        query = query.filter(InventoryTransaction.supplier_id == supplier_id)
    if transaction_type:
        query = query.filter(InventoryTransaction.transaction_type == transaction_type)
    if user_id:
        query = query.filter(InventoryTransaction.created_by_user_id == user_id)
    if date_from:
        query = query.filter(InventoryTransaction.created_at >= date_from)
    if date_to:
        query = query.filter(InventoryTransaction.created_at <= date_to)
    return query.order_by(InventoryTransaction.created_at.asc(), InventoryTransaction.id.asc()).all()


def product_cost_profile(db: Session, product_id: int) -> dict:
    product = db.get(Product, product_id)
    if product is None:
        raise ValueError("Product not found.")
    purchases = (
        db.query(InventoryTransaction)
        .filter(InventoryTransaction.product_id == product_id, InventoryTransaction.transaction_type == "purchase")
        .order_by(InventoryTransaction.created_at.desc(), InventoryTransaction.id.desc())
        .all()
    )
    purchase_costs = [parse_decimal(row.unit_cost_ex_vat) for row in purchases]
    last_purchase = purchases[0] if purchases else None
    return {
        "product": product,
        "current_quantity": product.current_inventory_quantity or Decimal("0"),
        "inventory_value": product.current_inventory_value_ex_vat or Decimal("0"),
        "weighted_average_cost": product.current_weighted_average_cost_ex_vat,
        "last_purchase_price": last_purchase.unit_cost_ex_vat if last_purchase else None,
        "highest_purchase_price": max(purchase_costs) if purchase_costs else None,
        "lowest_purchase_price": min(purchase_costs) if purchase_costs else None,
        "last_supplier": last_purchase.supplier if last_purchase else None,
        "last_purchase_date": last_purchase.created_at if last_purchase else None,
        "purchase_history": purchases,
        "ledger_state": _ledger_state_from_transactions(db, product_id),
    }


def inventory_valuation(db: Session) -> dict:
    by_product = defaultdict(lambda: {"quantity": Decimal("0"), "value": Decimal("0")})
    by_warehouse = defaultdict(lambda: {"warehouse": None, "value": Decimal("0")})
    transactions = db.query(InventoryTransaction).all()
    for transaction in transactions:
        by_product[transaction.product_id]["product"] = transaction.product
        by_product[transaction.product_id]["quantity"] += parse_decimal(transaction.quantity_change or 0)
        by_product[transaction.product_id]["value"] += parse_decimal(transaction.total_inventory_cost or 0)
        if transaction.warehouse_id:
            by_warehouse[transaction.warehouse_id]["warehouse"] = transaction.warehouse
            by_warehouse[transaction.warehouse_id]["value"] += parse_decimal(transaction.total_inventory_cost or 0)
    products_without_cost = (
        db.query(Product)
        .filter(Product.is_stock_item.is_(True), Product.current_weighted_average_cost_ex_vat.is_(None))
        .all()
    )
    transactions_total = sum((parse_decimal(transaction.total_inventory_cost) for transaction in transactions), Decimal("0"))
    return {
        "total_inventory_value_ex_vat": money(transactions_total),
        "transaction_ledger_value_ex_vat": money(transactions_total),
        "movement_ledger_value_ex_vat": money(transactions_total),
        "by_product": list(by_product.values()),
        "by_warehouse": list(by_warehouse.values()),
        "products_without_cost": products_without_cost,
        "recent_cost_changes": (
            db.query(InventoryTransaction)
            .filter(InventoryTransaction.weighted_average_cost_after.is_not(None))
            .order_by(InventoryTransaction.created_at.desc(), InventoryTransaction.id.desc())
            .limit(20)
            .all()
        ),
    }
