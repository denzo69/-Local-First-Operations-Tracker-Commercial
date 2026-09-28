from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import (
    GoodsReceipt,
    GoodsReceiptLine,
    InventoryBalance,
    Product,
    PurchaseOrder,
    PurchaseOrderLine,
    Supplier,
    User,
    WarehouseLocation,
    utc_now,
)
from app.services.audit_service import log_audit_event
from app.services.inventory_service import require_inventory_operational_user
from app.services.money_service import money, parse_decimal


def _positive_quantity(value, field: str) -> Decimal:
    try:
        parsed = parse_decimal(value)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a valid positive quantity.") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ValueError(f"{field} must be a positive quantity.")
    return parsed.quantize(Decimal("0.001"))


def _order_number(db: Session, order_date: date) -> str:
    prefix = f"PO-{order_date:%Y}-"
    existing = [row[0] for row in db.query(PurchaseOrder.order_number).filter(PurchaseOrder.order_number.like(f"{prefix}%")).all()]
    sequence = max((int(number.removeprefix(prefix)) for number in existing if number.removeprefix(prefix).isdigit()), default=0) + 1
    return f"{prefix}{sequence:05d}"


def create_purchase_order(
    db: Session,
    *,
    supplier_id: int,
    created_by_user_id: int,
    order_date: date | None = None,
    expected_date: date | None = None,
    notes: str = "",
) -> PurchaseOrder:
    user = require_inventory_operational_user(db.get(User, created_by_user_id))
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or not supplier.is_active:
        raise ValueError("An active supplier is required.")
    order_date = order_date or date.today()
    if expected_date and expected_date < order_date:
        raise ValueError("Expected date cannot be before the order date.")
    order = PurchaseOrder(
        order_number=_order_number(db, order_date),
        supplier_id=supplier.id,
        status="draft",
        order_date=order_date,
        expected_date=expected_date,
        created_by_user_id=user.id,
        notes=notes.strip() or None,
    )
    db.add(order)
    db.flush()
    log_audit_event(db, event_type="purchase_order.created", entity_type="purchase_order", entity_id=order.id, description=f"Purchase order {order.order_number} created.")
    db.commit()
    db.refresh(order)
    return order


def add_purchase_order_line(
    db: Session,
    *,
    purchase_order_id: int,
    product_id: int,
    destination_location_id: int,
    ordered_quantity,
    unit_cost_ex_vat,
    vat_rate=24,
) -> PurchaseOrderLine:
    order = db.get(PurchaseOrder, purchase_order_id)
    product = db.get(Product, product_id)
    location = db.get(WarehouseLocation, destination_location_id)
    if order is None:
        raise ValueError("Purchase order not found.")
    if order.status != "draft":
        raise ValueError("Only draft purchase orders can be edited.")
    if product is None or not product.is_active or not product.is_stock_item:
        raise ValueError("An active stock product is required.")
    if location is None or not location.is_active:
        raise ValueError("An active destination location is required.")
    qty = _positive_quantity(ordered_quantity, "Ordered quantity")
    try:
        cost = money(parse_decimal(unit_cost_ex_vat))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Unit cost must be a valid non-negative amount.") from exc
    try:
        vat = parse_decimal(vat_rate)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("VAT rate must be between 0 and 100.") from exc
    if not cost.is_finite() or cost < 0:
        raise ValueError("Unit cost cannot be negative.")
    if not vat.is_finite() or vat < 0 or vat > 100:
        raise ValueError("VAT rate must be between 0 and 100.")
    line = PurchaseOrderLine(
        purchase_order_id=order.id,
        product_id=product.id,
        destination_location_id=location.id,
        supplier_product_code=product.supplier_product_code,
        ordered_quantity=qty,
        received_quantity=Decimal("0.000"),
        unit_cost_ex_vat=cost,
        vat_rate=vat,
    )
    db.add(line)
    db.commit()
    db.refresh(line)
    return line


def order_purchase_order(db: Session, *, purchase_order_id: int) -> PurchaseOrder:
    order = db.get(PurchaseOrder, purchase_order_id)
    if order is None:
        raise ValueError("Purchase order not found.")
    if order.status != "draft":
        raise ValueError("Only draft purchase orders can be sent.")
    if not order.lines:
        raise ValueError("Add at least one line before sending a purchase order.")
    order.status = "ordered"
    order.ordered_at = utc_now()
    log_audit_event(db, event_type="purchase_order.ordered", entity_type="purchase_order", entity_id=order.id, description=f"Purchase order {order.order_number} sent to {order.supplier.name}.")
    db.commit()
    db.refresh(order)
    return order


def cancel_purchase_order(db: Session, *, purchase_order_id: int) -> PurchaseOrder:
    order = db.get(PurchaseOrder, purchase_order_id)
    if order is None:
        raise ValueError("Purchase order not found.")
    if order.status not in {"draft", "ordered", "partially_received"}:
        raise ValueError("Only an open purchase order can be cancelled.")
    if any(receipt.status == "draft" for receipt in order.goods_receipts):
        raise ValueError("Post or discard the draft goods receipt before cancelling this order.")
    order.status = "cancelled"
    order.cancelled_at = utc_now()
    log_audit_event(db, event_type="purchase_order.cancelled", entity_type="purchase_order", entity_id=order.id, description=f"Purchase order {order.order_number} cancelled.")
    db.commit()
    db.refresh(order)
    return order


def replenishment_suggestions(db: Session) -> list[dict]:
    suggestions = []
    products = db.query(Product).filter(Product.is_active.is_(True), Product.is_stock_item.is_(True)).order_by(Product.name.asc()).all()
    for product in products:
        reorder_point = parse_decimal(product.reorder_point or 0)
        target = parse_decimal(product.target_stock_quantity or 0)
        if reorder_point <= 0 or target <= reorder_point:
            continue
        available = sum((parse_decimal(row[0] or 0) for row in db.query(InventoryBalance.quantity_available).filter(InventoryBalance.product_id == product.id).all()), Decimal("0"))
        open_lines = (
            db.query(PurchaseOrderLine)
            .join(PurchaseOrder)
            .filter(PurchaseOrderLine.product_id == product.id, PurchaseOrder.status.in_(["ordered", "partially_received"]))
            .all()
        )
        on_order = sum((max(Decimal("0"), parse_decimal(line.ordered_quantity) - parse_decimal(line.received_quantity or 0)) for line in open_lines), Decimal("0"))
        net_available = available + on_order
        if net_available <= reorder_point:
            suggestions.append({
                "product": product,
                "available": available,
                "on_order": on_order,
                "suggested_quantity": max(Decimal("0"), (target - net_available).quantize(Decimal("0.001"))),
                "supplier": product.preferred_supplier,
                "unit_cost": parse_decimal(product.current_purchase_price_ex_vat or product.current_weighted_average_cost_ex_vat or 0),
            })
    return suggestions


def create_order_from_replenishment(
    db: Session,
    *,
    supplier_id: int,
    product_quantities: dict[int, str | Decimal],
    destination_location_id: int,
    created_by_user_id: int,
    expected_date: date | None = None,
) -> PurchaseOrder:
    suggestions = {item["product"].id: item for item in replenishment_suggestions(db)}
    selected = [(product_id, _positive_quantity(qty, "Suggested quantity")) for product_id, qty in product_quantities.items()]
    if not selected:
        raise ValueError("Select at least one product to order.")
    for product_id, _ in selected:
        item = suggestions.get(product_id)
        if item is None or item["supplier"] is None or item["supplier"].id != supplier_id:
            raise ValueError("Selected products must be current replenishment suggestions for the chosen supplier.")
    user = require_inventory_operational_user(db.get(User, created_by_user_id))
    supplier = db.get(Supplier, supplier_id)
    if supplier is None or not supplier.is_active:
        raise ValueError("An active supplier is required.")
    location = db.get(WarehouseLocation, destination_location_id)
    if location is None or not location.is_active:
        raise ValueError("An active destination location is required.")
    today = date.today()
    if expected_date and expected_date < today:
        raise ValueError("Expected date cannot be before the order date.")
    order = PurchaseOrder(
        order_number=_order_number(db, today),
        supplier_id=supplier.id,
        status="draft",
        order_date=today,
        expected_date=expected_date,
        created_by_user_id=user.id,
    )
    db.add(order)
    db.flush()
    try:
        for product_id, qty in selected:
            item = suggestions[product_id]
            db.add(PurchaseOrderLine(
                purchase_order_id=order.id,
                product_id=product_id,
                destination_location_id=destination_location_id,
                supplier_product_code=item["product"].supplier_product_code,
                ordered_quantity=qty,
                received_quantity=0,
                unit_cost_ex_vat=money(item["unit_cost"]),
                vat_rate=item["product"].vat_percent or 24,
            ))
        log_audit_event(db, event_type="purchase_order.created", entity_type="purchase_order", entity_id=order.id, description=f"Purchase order {order.order_number} created from replenishment suggestions.")
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(order)
    return order


def create_receipt_from_purchase_order(
    db: Session,
    *,
    purchase_order_id: int,
    quantities: dict[int, str | Decimal],
    received_by_user_id: int,
    receipt_date: date | None = None,
) -> GoodsReceipt:
    user = require_inventory_operational_user(db.get(User, received_by_user_id))
    order = db.get(PurchaseOrder, purchase_order_id)
    if order is None:
        raise ValueError("Purchase order not found.")
    if order.status not in {"ordered", "partially_received"}:
        raise ValueError("Only sent purchase orders can be received.")
    pending = [receipt for receipt in order.goods_receipts if receipt.status == "draft"]
    if pending:
        raise ValueError("Post or cancel the existing draft receipt before creating another.")
    line_values = []
    for line in order.lines:
        remaining = parse_decimal(line.ordered_quantity) - parse_decimal(line.received_quantity or 0)
        raw = quantities.get(line.id, "0")
        try:
            qty = parse_decimal(raw)
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise ValueError("Received quantities must be valid zero or positive numbers.") from exc
        if not qty.is_finite() or qty < 0:
            raise ValueError("Received quantities must be zero or positive.")
        qty = qty.quantize(Decimal("0.001"))
        if qty > remaining:
            raise ValueError(f"Received quantity exceeds the outstanding quantity for {line.product.name}.")
        if qty > 0:
            line_values.append((line, qty))
    if not line_values:
        raise ValueError("Enter a positive received quantity for at least one line.")
    receipt = GoodsReceipt(
        supplier_id=order.supplier_id,
        purchase_order_id=order.id,
        receipt_date=receipt_date or date.today(),
        received_by_user_id=user.id,
        status="draft",
        allocation_method="by_value",
        notes=f"Created from {order.order_number}",
    )
    db.add(receipt)
    db.flush()
    for line, qty in line_values:
        inc = money(parse_decimal(line.unit_cost_ex_vat) * (1 + parse_decimal(line.vat_rate) / Decimal("100")))
        db.add(GoodsReceiptLine(
            goods_receipt_id=receipt.id,
            purchase_order_line_id=line.id,
            product_id=line.product_id,
            destination_location_id=line.destination_location_id,
            quantity=qty,
            purchase_unit_price_ex_vat=line.unit_cost_ex_vat,
            vat_rate=line.vat_rate,
            purchase_unit_price_inc_vat=inc,
        ))
    log_audit_event(db, event_type="purchase_order.receipt_created", entity_type="purchase_order", entity_id=order.id, description=f"Draft goods receipt {receipt.id} created from {order.order_number}.")
    db.commit()
    db.refresh(receipt)
    return receipt


def refresh_purchase_order_receipt_status(db: Session, order: PurchaseOrder) -> None:
    if order.status == "cancelled":
        return
    total = sum((parse_decimal(line.ordered_quantity) for line in order.lines), Decimal("0"))
    received = sum((parse_decimal(line.received_quantity or 0) for line in order.lines), Decimal("0"))
    if received <= 0:
        order.status = "ordered"
    elif received >= total:
        order.status = "received"
    else:
        order.status = "partially_received"
