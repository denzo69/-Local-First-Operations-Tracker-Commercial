from datetime import UTC, datetime

from sqlalchemy import Boolean, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.orm import relationship

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(UTC)


class Customer(Base):
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    phone = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    address = Column(Text, nullable=True)
    company_name = Column(String(255), nullable=True)
    business_id = Column(String(100), nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    jobs = relationship("Job", back_populates="customer")
    sales = relationship("Sale", back_populates="customer")


class JobStatus(Base):
    __tablename__ = "job_statuses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    sort_order = Column(Integer, default=0)
    is_final = Column(Boolean, default=False)
    is_ready_state = Column(Boolean, default=False)
    is_packed_state = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)

    jobs = relationship("Job", back_populates="status")


class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    job_number = Column(String(100), nullable=True, index=True)
    receipt_number = Column(String(100), nullable=True, unique=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    arrival_date = Column(Date, nullable=True)
    requested_pickup_date = Column(Date, nullable=True, index=True)
    status_id = Column(Integer, ForeignKey("job_statuses.id"), nullable=True)
    priority = Column(String(50), default="normal")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    customer = relationship("Customer", back_populates="jobs")
    status = relationship("JobStatus", back_populates="jobs")
    items = relationship("JobItem", back_populates="job")
    sales = relationship("Sale", back_populates="work_order")


class Product(Base):
    __tablename__ = "products"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    description = Column(Text, nullable=True)
    unit_price = Column(Numeric(12, 2), default=0)
    vat_percent = Column(Numeric(5, 2), default=24)
    unit = Column(String(50), default="pcs")
    is_active = Column(Boolean, default=True)
    is_stock_item = Column(Boolean, default=False)
    current_weighted_average_cost_ex_vat = Column(Numeric(18, 6), nullable=True)
    current_inventory_quantity = Column(Numeric(18, 3), default=0)
    current_inventory_value_ex_vat = Column(Numeric(18, 2), default=0)
    current_purchase_price_ex_vat = Column(Numeric(12, 2), nullable=True)
    current_purchase_price_inc_vat = Column(Numeric(12, 2), nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    job_items = relationship("JobItem", back_populates="product")
    inventory_balances = relationship("InventoryBalance", back_populates="product")
    inventory_transactions = relationship("InventoryTransaction", back_populates="product")


class Supplier(Base):
    __tablename__ = "suppliers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    business_id = Column(String(100), nullable=True)
    contact_name = Column(String(255), nullable=True)
    email = Column(String(255), nullable=True)
    phone = Column(String(100), nullable=True)
    address = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    goods_receipts = relationship("GoodsReceipt", back_populates="supplier")


class Warehouse(Base):
    __tablename__ = "warehouses"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    code = Column(String(50), nullable=False, unique=True, index=True)
    description = Column(Text, nullable=True)
    address = Column(Text, nullable=True)
    is_external = Column(Boolean, default=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    locations = relationship("WarehouseLocation", back_populates="warehouse")


class WarehouseLocation(Base):
    __tablename__ = "warehouse_locations"

    id = Column(Integer, primary_key=True, index=True)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False)
    parent_id = Column(Integer, ForeignKey("warehouse_locations.id"), nullable=True)
    code = Column(String(50), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    location_type = Column(String(50), default="bin")
    is_active = Column(Boolean, default=True)
    sort_order = Column(Integer, default=0)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    warehouse = relationship("Warehouse", back_populates="locations")
    parent = relationship("WarehouseLocation", remote_side=[id])
    balances = relationship("InventoryBalance", back_populates="warehouse_location")


class InventoryBalance(Base):
    __tablename__ = "inventory_balances"
    __table_args__ = (
        UniqueConstraint("product_id", "warehouse_location_id", name="ux_inventory_balance_product_location"),
    )

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    warehouse_location_id = Column(Integer, ForeignKey("warehouse_locations.id"), nullable=False)
    quantity_on_hand = Column(Numeric(18, 3), default=0)
    quantity_reserved = Column(Numeric(18, 3), default=0)
    quantity_available = Column(Numeric(18, 3), default=0)
    weighted_average_cost_ex_vat = Column(Numeric(18, 6), nullable=True)
    inventory_value_ex_vat = Column(Numeric(18, 2), default=0)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    product = relationship("Product", back_populates="inventory_balances")
    warehouse_location = relationship("WarehouseLocation", back_populates="balances")


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"

    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    receipt_date = Column(Date, nullable=False, index=True)
    delivery_number = Column(String(100), nullable=True, index=True)
    invoice_number = Column(String(100), nullable=True, index=True)
    freight_total_ex_vat = Column(Numeric(12, 2), default=0)
    freight_vat_rate = Column(Numeric(5, 2), default=0)
    freight_vat_amount = Column(Numeric(12, 2), default=0)
    freight_total_inc_vat = Column(Numeric(12, 2), default=0)
    other_costs_total_ex_vat = Column(Numeric(12, 2), default=0)
    other_costs_vat_rate = Column(Numeric(5, 2), default=0)
    other_costs_vat_amount = Column(Numeric(12, 2), default=0)
    other_costs_total_inc_vat = Column(Numeric(12, 2), default=0)
    allocation_method = Column(String(50), default="by_value")
    status = Column(String(50), default="draft", index=True)
    received_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    posted_at = Column(DateTime, nullable=True)
    cancelled_at = Column(DateTime, nullable=True)
    cancellation_reason = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    supplier = relationship("Supplier", back_populates="goods_receipts")
    received_by = relationship("User", foreign_keys=[received_by_user_id])
    lines = relationship("GoodsReceiptLine", back_populates="goods_receipt")
    transactions = relationship("InventoryTransaction", back_populates="goods_receipt")


class GoodsReceiptLine(Base):
    __tablename__ = "goods_receipt_lines"

    id = Column(Integer, primary_key=True, index=True)
    goods_receipt_id = Column(Integer, ForeignKey("goods_receipts.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    destination_location_id = Column(Integer, ForeignKey("warehouse_locations.id"), nullable=False)
    quantity = Column(Numeric(18, 3), nullable=False)
    purchase_unit_price_ex_vat = Column(Numeric(12, 2), nullable=False)
    vat_rate = Column(Numeric(5, 2), default=24)
    purchase_unit_price_inc_vat = Column(Numeric(12, 2), nullable=False)
    allocated_freight_ex_vat = Column(Numeric(12, 2), default=0)
    allocated_other_costs_ex_vat = Column(Numeric(12, 2), default=0)
    landed_unit_cost_ex_vat = Column(Numeric(18, 6), nullable=True)
    line_total_ex_vat = Column(Numeric(12, 2), default=0)
    created_at = Column(DateTime, default=utc_now)

    goods_receipt = relationship("GoodsReceipt", back_populates="lines")
    product = relationship("Product")
    destination_location = relationship("WarehouseLocation")


class InventoryTransaction(Base):
    __tablename__ = "inventory_transactions"

    id = Column(Integer, primary_key=True, index=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=False)
    warehouse_id = Column(Integer, ForeignKey("warehouses.id"), nullable=False, index=True)
    shelf_location_id = Column(Integer, ForeignKey("warehouse_locations.id"), nullable=True, index=True)
    transaction_type = Column(String(50), nullable=False, index=True)
    quantity_change = Column(Numeric(18, 3), nullable=False)
    unit_cost_ex_vat = Column(Numeric(18, 6), nullable=False)
    allocated_freight_cost = Column(Numeric(12, 2), default=0)
    allocated_other_cost = Column(Numeric(12, 2), default=0)
    total_inventory_cost = Column(Numeric(18, 2), nullable=False)
    inventory_value_before = Column(Numeric(18, 2), nullable=False)
    inventory_value_after = Column(Numeric(18, 2), nullable=False)
    stock_before = Column(Numeric(18, 3), nullable=False)
    stock_after = Column(Numeric(18, 3), nullable=False)
    weighted_average_cost_before = Column(Numeric(18, 6), nullable=True)
    weighted_average_cost_after = Column(Numeric(18, 6), nullable=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=True, index=True)
    purchase_invoice_number = Column(String(100), nullable=True, index=True)
    delivery_note_number = Column(String(100), nullable=True, index=True)
    goods_receipt_id = Column(Integer, ForeignKey("goods_receipts.id"), nullable=True)
    work_order_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=True)
    adjustment_reason = Column(Text, nullable=True)
    reference = Column(String(255), nullable=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime, default=utc_now, index=True)
    reversal_of_transaction_id = Column(Integer, ForeignKey("inventory_transactions.id"), nullable=True)

    product = relationship("Product", back_populates="inventory_transactions")
    warehouse = relationship("Warehouse")
    shelf_location = relationship("WarehouseLocation", foreign_keys=[shelf_location_id])
    supplier = relationship("Supplier")
    goods_receipt = relationship("GoodsReceipt", back_populates="transactions")
    work_order = relationship("Job")
    sale = relationship("Sale", back_populates="inventory_transactions")
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    reversal_of_transaction = relationship("InventoryTransaction", remote_side=[id])


@event.listens_for(InventoryTransaction, "before_update")
def prevent_inventory_transaction_update(mapper, connection, target):
    raise ValueError("Inventory transactions are immutable. Create a correction transaction instead.")


@event.listens_for(InventoryTransaction, "before_delete")
def prevent_inventory_transaction_delete(mapper, connection, target):
    raise ValueError("Inventory transactions are immutable. Create a correction transaction instead.")


class JobItem(Base):
    __tablename__ = "job_items"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    description = Column(String(255), nullable=False)
    quantity = Column(Numeric(12, 3), default=1)
    unit_price = Column(Numeric(12, 2), default=0)
    vat_percent = Column(Numeric(5, 2), default=24)
    line_total = Column(Numeric(12, 2), default=0)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    job = relationship("Job", back_populates="items")
    product = relationship("Product", back_populates="job_items")


class Receipt(Base):
    __tablename__ = "receipts"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)
    receipt_number = Column(String(100), nullable=False, unique=True, index=True)
    receipt_type = Column(String(100), default="incoming")
    printed_at = Column(DateTime, nullable=True)
    editable_snapshot = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)


class Setting(Base):
    __tablename__ = "settings"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String(255), unique=True, nullable=False, index=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)


class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True, index=True)
    event_type = Column(String(100), nullable=False)
    entity_type = Column(String(100), nullable=True)
    entity_id = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)


class Role(Base):
    __tablename__ = "roles"

    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    created_at = Column(DateTime, default=utc_now)

    users = relationship("User", back_populates="role")


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    login_name = Column(String(255), nullable=True, unique=True, index=True)
    password_hash = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    can_receive_sales_credit = Column(Boolean, default=False)
    role_id = Column(Integer, ForeignKey("roles.id"), nullable=False)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    role = relationship("Role", back_populates="users")
    shifts = relationship("Shift", back_populates="seller")
    sales = relationship("Sale", foreign_keys="Sale.seller_id", back_populates="seller")


class CashRegister(Base):
    __tablename__ = "cash_registers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    location = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)
    updated_at = Column(DateTime, default=utc_now, onupdate=utc_now)

    shifts = relationship("Shift", back_populates="cash_register")


class Shift(Base):
    __tablename__ = "shifts"

    id = Column(Integer, primary_key=True, index=True)
    cash_register_id = Column(Integer, ForeignKey("cash_registers.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    opened_at = Column(DateTime, default=utc_now)
    closed_at = Column(DateTime, nullable=True)
    business_date = Column(Date, nullable=False, index=True)
    starting_cash = Column(Numeric(12, 2), default=0)
    counted_closing_cash = Column(Numeric(12, 2), nullable=True)
    expected_closing_cash = Column(Numeric(12, 2), nullable=True)
    cash_over_short = Column(Numeric(12, 2), nullable=True)
    status = Column(String(50), default="open", index=True)
    notes = Column(Text, nullable=True)

    cash_register = relationship("CashRegister", back_populates="shifts")
    seller = relationship("User", back_populates="shifts")
    sales = relationship("Sale", back_populates="shift")
    cash_movements = relationship("CashMovement", back_populates="shift")
    payments = relationship("Payment", back_populates="shift")
    refunds = relationship("Refund", back_populates="shift")


class Sale(Base):
    __tablename__ = "sales"

    id = Column(Integer, primary_key=True, index=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    sold_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    cash_register_id = Column(Integer, ForeignKey("cash_registers.id"), nullable=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True, index=True)
    customer_name_snapshot = Column(String(255), nullable=True)
    work_order_id = Column(Integer, ForeignKey("jobs.id"), nullable=True)
    source_type = Column(String(50), default="pos", index=True)
    idempotency_key = Column(String(100), nullable=True, unique=True, index=True)
    document_number = Column(String(100), nullable=True, unique=True, index=True)
    created_at = Column(DateTime, default=utc_now)
    sold_at = Column(DateTime, default=utc_now, index=True)
    business_date = Column(Date, nullable=True, index=True)
    finalized_at = Column(DateTime, nullable=True)
    payment_method = Column(String(50), nullable=False)
    settlement_status = Column(String(50), default="paid", index=True)
    invoice_customer_snapshot_json = Column(Text, nullable=True)
    transferred_to_invoicing_at = Column(DateTime, nullable=True)
    external_invoice_service = Column(String(120), nullable=True)
    external_invoice_number = Column(String(120), nullable=True, index=True)
    invoice_date = Column(Date, nullable=True)
    due_date = Column(Date, nullable=True, index=True)
    external_invoice_reference = Column(String(255), nullable=True)
    invoice_handoff_notes = Column(Text, nullable=True)
    payment_status_checked_at = Column(DateTime, nullable=True)
    paid_at = Column(DateTime, nullable=True)
    next_follow_up_at = Column(DateTime, nullable=True, index=True)
    reminder_count = Column(Integer, default=0)
    last_reminder_sent_at = Column(DateTime, nullable=True)
    follow_up_notes = Column(Text, nullable=True)
    subtotal = Column(Numeric(12, 2), default=0)
    vat_total = Column(Numeric(12, 2), default=0)
    discount_total = Column(Numeric(12, 2), default=0)
    total = Column(Numeric(12, 2), default=0)
    cost_of_goods_sold_ex_vat = Column(Numeric(12, 2), default=0)
    gross_profit_ex_vat = Column(Numeric(12, 2), default=0)
    gross_margin_percent = Column(Numeric(7, 3), nullable=True)
    vat_breakdown_json = Column(Text, nullable=True)
    status = Column(String(50), default="completed", index=True)
    seller_override_reason = Column(Text, nullable=True)
    seller_overridden_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    seller_overridden_at = Column(DateTime, nullable=True)

    seller = relationship("User", foreign_keys=[seller_id], back_populates="sales")
    sold_by = relationship("User", foreign_keys=[sold_by_user_id])
    created_by = relationship("User", foreign_keys=[created_by_user_id])
    seller_overridden_by = relationship("User", foreign_keys=[seller_overridden_by_user_id])
    cash_register = relationship("CashRegister")
    customer = relationship("Customer", back_populates="sales")
    shift = relationship("Shift", back_populates="sales")
    work_order = relationship("Job", back_populates="sales")
    lines = relationship("SaleLine", back_populates="sale")
    payments = relationship("Payment", back_populates="sale")
    refunds = relationship("Refund", back_populates="sale")
    inventory_transactions = relationship("InventoryTransaction", back_populates="sale")


class SaleLine(Base):
    __tablename__ = "sale_lines"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    work_order_item_id = Column(Integer, ForeignKey("job_items.id"), nullable=True)
    product_id = Column(Integer, ForeignKey("products.id"), nullable=True)
    description_snapshot = Column(String(255), nullable=False)
    quantity = Column(Numeric(12, 3), default=1)
    unit_price = Column(Numeric(12, 2), default=0)
    vat_percent = Column(Numeric(5, 2), default=24)
    discount_amount = Column(Numeric(12, 2), default=0)
    line_total = Column(Numeric(12, 2), default=0)
    vat_amount = Column(Numeric(12, 2), default=0)
    cost_of_goods_sold_ex_vat = Column(Numeric(12, 2), default=0)
    gross_profit_ex_vat = Column(Numeric(12, 2), default=0)
    gross_margin_percent = Column(Numeric(7, 3), nullable=True)
    created_at = Column(DateTime, default=utc_now)

    sale = relationship("Sale", back_populates="lines")
    product = relationship("Product")
    work_order_item = relationship("JobItem")


class Payment(Base):
    __tablename__ = "payments"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    received_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    payment_method = Column(String(50), nullable=False, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    paid_at = Column(DateTime, default=utc_now)
    reference = Column(String(255), nullable=True)

    sale = relationship("Sale", back_populates="payments")
    shift = relationship("Shift", back_populates="payments")
    seller = relationship("User", foreign_keys=[seller_id])
    received_by = relationship("User", foreign_keys=[received_by_user_id])


class Refund(Base):
    __tablename__ = "refunds"

    id = Column(Integer, primary_key=True, index=True)
    sale_id = Column(Integer, ForeignKey("sales.id"), nullable=False)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=True)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    business_date = Column(Date, nullable=True, index=True)
    amount = Column(Numeric(12, 2), nullable=False)
    vat_amount = Column(Numeric(12, 2), default=0)
    vat_breakdown_json = Column(Text, nullable=True)
    reason = Column(Text, nullable=True)
    refunded_at = Column(DateTime, default=utc_now)
    payment_method = Column(String(50), nullable=False)

    sale = relationship("Sale", back_populates="refunds")
    shift = relationship("Shift", back_populates="refunds")
    seller = relationship("User")


class CashMovement(Base):
    __tablename__ = "cash_movements"

    id = Column(Integer, primary_key=True, index=True)
    shift_id = Column(Integer, ForeignKey("shifts.id"), nullable=False)
    seller_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    movement_type = Column(String(50), nullable=False)
    amount = Column(Numeric(12, 2), nullable=False)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    shift = relationship("Shift", back_populates="cash_movements")
    seller = relationship("User")


class DailyClosing(Base):
    __tablename__ = "daily_closings"

    id = Column(Integer, primary_key=True, index=True)
    business_date = Column(Date, nullable=False, unique=True, index=True)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    closed_at = Column(DateTime, default=utc_now)
    reopened_at = Column(DateTime, nullable=True)
    reopened_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    reopen_reason = Column(Text, nullable=True)
    status = Column(String(50), default="closed", index=True)
    current_version = Column(Integer, default=0)
    total_sales = Column(Numeric(12, 2), default=0)
    total_refunds = Column(Numeric(12, 2), default=0)
    total_discounts = Column(Numeric(12, 2), default=0)
    expected_cash = Column(Numeric(12, 2), default=0)
    counted_cash = Column(Numeric(12, 2), default=0)
    cash_over_short = Column(Numeric(12, 2), default=0)

    created_by = relationship("User", foreign_keys=[created_by_user_id])
    reopened_by = relationship("User", foreign_keys=[reopened_by_user_id])
    snapshots = relationship("DailyClosingSnapshot", back_populates="daily_closing")


class DailyClosingSnapshot(Base):
    __tablename__ = "daily_closing_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    daily_closing_id = Column(Integer, ForeignKey("daily_closings.id"), nullable=False)
    version = Column(Integer, default=1, nullable=False)
    schema_version = Column(Integer, default=1, nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    snapshot_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=utc_now)

    daily_closing = relationship("DailyClosing", back_populates="snapshots")
    created_by = relationship("User")
