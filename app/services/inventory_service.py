from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from sqlalchemy.orm import Session

from app.models import (
    AuditLog,
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryBalance,
    InventoryMovement,
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
    other_costs_total_ex_vat="0",
    allocation_method: str = "by_value",
    notes: str = "",
) -> GoodsReceipt:
    creator = require_inventory_manager(db.get(User, received_by_user_id))
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or not supplier.is_active:
        raise ValueError("Active supplier is required.")
    if allocation_method not in ALLOCATION_METHODS:
        raise ValueError("Invalid landed cost allocation method.")
    receipt = GoodsReceipt(
        supplier_id=supplier_id,
        receipt_date=receipt_date,
        delivery_number=delivery_number.strip() or None,
        invoice_number=invoice_number.strip() or None,
        freight_total_ex_vat=require_non_negative_money(freight_total_ex_vat, "Freight total ex VAT"),
        other_costs_total_ex_vat=require_non_negative_money(other_costs_total_ex_vat, "Other costs total ex VAT"),
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
    lines = list(receipt.lines)
    freight = require_non_negative_money(receipt.freight_total_ex_vat, "Freight total ex VAT")
    other = require_non_negative_money(receipt.other_costs_total_ex_vat, "Other costs total ex VAT")
    allocations = allocate_landed_costs(lines, freight, other, receipt.allocation_method or "by_value")

    line_previews = []
    purchase_total = Decimal("0")
    vat_total = Decimal("0")
    landed_total = Decimal("0")
    projected_by_product: dict[int, tuple[Decimal, Decimal, Decimal]] = {}
    for line in lines:
        qty = parse_decimal(line.quantity)
        line_purchase = money(qty * parse_decimal(line.purchase_unit_price_ex_vat))
        allocated_freight = allocations[line.id]["freight"]
        allocated_other = allocations[line.id]["other"]
        landed_line_total = money(line_purchase + allocated_freight + allocated_other)
        landed_unit = cost(landed_line_total / qty)
        old_qty = parse_decimal(line.product.current_inventory_quantity or 0)
        old_avg = parse_decimal(line.product.current_weighted_average_cost_ex_vat or 0)
        old_value = money(parse_decimal(line.product.current_inventory_value_ex_vat if line.product.current_inventory_value_ex_vat is not None else old_qty * old_avg))
        if old_qty < 0:
            raise ValueError("Negative stock must be corrected before posting receipts.")
        new_qty = quantity(old_qty + qty)
        new_value = money(old_value + landed_line_total)
        new_avg = cost(new_value / new_qty)
        projected_by_product[line.product_id] = (new_qty, new_value, new_avg)
        vat_amount = money(line_purchase * parse_decimal(line.vat_rate) / Decimal("100"))
        purchase_total += line_purchase
        vat_total += vat_amount
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

    return {
        "receipt": receipt,
        "lines": line_previews,
        "purchase_total_ex_vat": money(purchase_total),
        "freight_total_ex_vat": freight,
        "other_costs_total_ex_vat": other,
        "total_landed_cost_ex_vat": money(landed_total),
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


def post_goods_receipt(db: Session, *, goods_receipt_id: int, posted_by_user_id: int) -> GoodsReceipt:
    user = require_inventory_manager(db.get(User, posted_by_user_id))
    receipt = db.get(GoodsReceipt, goods_receipt_id)
    if receipt is None:
        raise ValueError("Goods receipt not found.")
    if receipt.status != "draft":
        raise ValueError("Only draft goods receipts can be posted.")
    preview = preview_goods_receipt(db, receipt)

    for item in preview["lines"]:
        line = item["line"]
        product = line.product
        balance = _get_or_create_balance(
            db,
            product_id=line.product_id,
            location_id=line.destination_location_id,
        )
        old_product_qty = quantity(parse_decimal(product.current_inventory_quantity or 0))
        old_product_avg = cost(parse_decimal(product.current_weighted_average_cost_ex_vat or 0))
        old_product_value = money(parse_decimal(product.current_inventory_value_ex_vat if product.current_inventory_value_ex_vat is not None else old_product_qty * old_product_avg))
        if old_product_qty < 0:
            raise ValueError("Negative stock must be corrected before posting receipts.")

        received_qty = quantity(parse_decimal(line.quantity))
        landed_line_total = money(item["purchase_value_ex_vat"] + item["allocated_freight_ex_vat"] + item["allocated_other_costs_ex_vat"])
        new_product_qty = quantity(old_product_qty + received_qty)
        if new_product_qty <= 0:
            raise ValueError("Posting receipt would not create positive inventory quantity.")
        new_product_value = money(old_product_value + landed_line_total)
        new_product_avg = cost(new_product_value / new_product_qty)

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

        product.current_inventory_quantity = new_product_qty
        product.current_inventory_value_ex_vat = new_product_value
        product.current_weighted_average_cost_ex_vat = new_product_avg
        product.current_purchase_price_ex_vat = line.purchase_unit_price_ex_vat
        product.current_purchase_price_inc_vat = line.purchase_unit_price_inc_vat
        product.updated_at = utc_now()

        movement = InventoryMovement(
            product_id=line.product_id,
            movement_type="goods_receipt",
            quantity=received_qty,
            warehouse_location_id=line.destination_location_id,
            to_location_id=line.destination_location_id,
            unit_cost_ex_vat=item["landed_unit_cost_ex_vat"],
            total_cost_ex_vat=landed_line_total,
            goods_receipt_id=receipt.id,
            reference=receipt.delivery_number,
            created_by_user_id=user.id,
            old_average_cost_ex_vat=old_product_avg,
            new_average_cost_ex_vat=new_product_avg,
            old_quantity=old_product_qty,
            new_quantity=new_product_qty,
            old_inventory_value_ex_vat=old_product_value,
            new_inventory_value_ex_vat=new_product_value,
        )
        db.add(movement)

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
    original_movements = [
        movement
        for movement in receipt.movements
        if movement.movement_type == "goods_receipt" and movement.reversal_of_movement_id is None
    ]
    for movement in original_movements:
        product = movement.product
        balance = _get_or_create_balance(
            db,
            product_id=movement.product_id,
            location_id=movement.to_location_id or movement.warehouse_location_id,
        )
        old_qty = quantity(parse_decimal(product.current_inventory_quantity or 0))
        old_avg = cost(parse_decimal(product.current_weighted_average_cost_ex_vat or 0))
        old_value = money(parse_decimal(product.current_inventory_value_ex_vat or 0))
        reverse_qty = quantity(parse_decimal(movement.quantity))
        reverse_value = money(movement.total_cost_ex_vat)
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

        product.current_inventory_quantity = new_qty
        product.current_inventory_value_ex_vat = new_value
        product.current_weighted_average_cost_ex_vat = new_avg
        product.updated_at = utc_now()
        reversal = InventoryMovement(
            product_id=movement.product_id,
            movement_type="reversal",
            quantity=quantity(-reverse_qty),
            warehouse_location_id=movement.warehouse_location_id,
            from_location_id=movement.to_location_id or movement.warehouse_location_id,
            unit_cost_ex_vat=movement.unit_cost_ex_vat,
            total_cost_ex_vat=money(-reverse_value),
            goods_receipt_id=receipt.id,
            reference=f"Reversal of movement {movement.id}",
            created_by_user_id=user.id,
            old_average_cost_ex_vat=old_avg,
            new_average_cost_ex_vat=new_avg,
            old_quantity=old_qty,
            new_quantity=new_qty,
            old_inventory_value_ex_vat=old_value,
            new_inventory_value_ex_vat=new_value,
            reversal_of_movement_id=movement.id,
        )
        db.add(reversal)

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
) -> InventoryMovement:
    user = require_inventory_manager(db.get(User, created_by_user_id))
    product = db.get(Product, product_id)
    if product is None or not product.is_stock_item:
        raise ValueError("Stock product is required.")
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

    product.current_inventory_quantity = new_qty
    product.current_inventory_value_ex_vat = new_value
    product.current_weighted_average_cost_ex_vat = new_avg
    movement = InventoryMovement(
        product_id=product_id,
        movement_type="sale_issue",
        quantity=quantity(-qty),
        warehouse_location_id=warehouse_location_id,
        from_location_id=warehouse_location_id,
        unit_cost_ex_vat=old_avg,
        total_cost_ex_vat=money(-total_cost),
        sale_id=sale_id,
        created_by_user_id=user.id,
        old_average_cost_ex_vat=old_avg,
        new_average_cost_ex_vat=new_avg,
        old_quantity=old_qty,
        new_quantity=new_qty,
        old_inventory_value_ex_vat=old_value,
        new_inventory_value_ex_vat=new_value,
    )
    db.add(movement)
    db.flush()
    log_audit_event(
        db,
        event_type="inventory.sale_issue",
        entity_type="inventory_movement",
        entity_id=movement.id,
        description=f"Sale issue recorded for product {product.name}: {qty} at {old_avg}.",
    )
    db.commit()
    db.refresh(movement)
    return movement


def transfer_stock(
    db: Session,
    *,
    product_id: int,
    from_location_id: int,
    to_location_id: int,
    quantity_value,
    created_by_user_id: int,
) -> list[InventoryMovement]:
    user = require_inventory_manager(db.get(User, created_by_user_id))
    product = db.get(Product, product_id)
    if product is None or not product.is_stock_item:
        raise ValueError("Stock product is required.")
    source = _get_or_create_balance(db, product_id=product_id, location_id=from_location_id)
    target = _get_or_create_balance(db, product_id=product_id, location_id=to_location_id)
    qty = require_positive_quantity(quantity_value)
    avg = cost(parse_decimal(product.current_weighted_average_cost_ex_vat or 0))
    total_value = money(qty * avg)
    if parse_decimal(source.quantity_on_hand or 0) - qty < 0:
        raise ValueError("Negative stock is not allowed.")

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

    outgoing = InventoryMovement(
        product_id=product_id,
        movement_type="transfer",
        quantity=quantity(-qty),
        from_location_id=from_location_id,
        to_location_id=to_location_id,
        unit_cost_ex_vat=avg,
        total_cost_ex_vat=money(-total_value),
        created_by_user_id=user.id,
    )
    incoming = InventoryMovement(
        product_id=product_id,
        movement_type="transfer",
        quantity=qty,
        from_location_id=from_location_id,
        to_location_id=to_location_id,
        unit_cost_ex_vat=avg,
        total_cost_ex_vat=total_value,
        created_by_user_id=user.id,
    )
    db.add_all([outgoing, incoming])
    db.commit()
    db.refresh(outgoing)
    db.refresh(incoming)
    return [outgoing, incoming]


def inventory_valuation(db: Session) -> dict:
    balances = db.query(InventoryBalance).all()
    by_product = defaultdict(lambda: {"quantity": Decimal("0"), "value": Decimal("0")})
    by_warehouse = defaultdict(lambda: {"warehouse": None, "value": Decimal("0")})
    for balance in balances:
        by_product[balance.product_id]["product"] = balance.product
        by_product[balance.product_id]["quantity"] += parse_decimal(balance.quantity_on_hand or 0)
        by_product[balance.product_id]["value"] += parse_decimal(balance.inventory_value_ex_vat or 0)
        warehouse = balance.warehouse_location.warehouse
        by_warehouse[warehouse.id]["warehouse"] = warehouse
        by_warehouse[warehouse.id]["value"] += parse_decimal(balance.inventory_value_ex_vat or 0)
    products_without_cost = (
        db.query(Product)
        .filter(Product.is_stock_item.is_(True), Product.current_weighted_average_cost_ex_vat.is_(None))
        .all()
    )
    movements_total = sum((parse_decimal(movement.total_cost_ex_vat) for movement in db.query(InventoryMovement).all()), Decimal("0"))
    balances_total = sum((values["value"] for values in by_product.values()), Decimal("0"))
    return {
        "total_inventory_value_ex_vat": money(balances_total),
        "movement_ledger_value_ex_vat": money(movements_total),
        "by_product": list(by_product.values()),
        "by_warehouse": list(by_warehouse.values()),
        "products_without_cost": products_without_cost,
        "recent_cost_changes": (
            db.query(InventoryMovement)
            .filter(InventoryMovement.new_average_cost_ex_vat.is_not(None))
            .order_by(InventoryMovement.occurred_at.desc())
            .limit(20)
            .all()
        ),
    }
