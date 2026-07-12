# Database Design

## MVP tables

The MVP database should include the following tables:

- customers
- jobs
- job_statuses
- products
- job_items
- receipts
- settings
- audit_log
- roles
- users
- cash_registers
- shifts
- sales
- sale_lines
- payments
- refunds
- cash_movements
- daily_closings
- daily_closing_snapshots
- suppliers
- warehouses
- warehouse_locations
- inventory_balances
- goods_receipts
- goods_receipt_lines
- inventory_transactions

## Entity overview

```text
Customer 1---N Job
Job 1---N JobItem
Product 1---N JobItem
Job 1---N Receipt
User 1---N Shift
CashRegister 1---N Shift
Shift 1---N Sale
Sale 1---N Payment
Sale 1---N Refund
DailyClosing 1---N DailyClosingSnapshot
Supplier 1---N GoodsReceipt
Warehouse 1---N WarehouseLocation
Product 1---N InventoryTransaction
Product 1---N InventoryBalance
GoodsReceipt 1---N GoodsReceiptLine
GoodsReceipt 1---N InventoryTransaction
```

## customers

Planned fields:

- id
- name
- phone
- email
- address
- company_name
- business_id
- notes
- created_at
- updated_at

## jobs

Planned fields:

- id
- job_number
- receipt_number
- customer_id
- title
- description
- arrival_date
- requested_pickup_date
- status_id
- priority
- notes
- created_at
- updated_at

## job_statuses

Planned fields:

- id
- name
- sort_order
- is_final
- is_ready_state
- is_packed_state
- is_active

## products

Planned fields:

- id
- name
- description
- unit_price
- vat_percent
- unit
- is_active
- is_stock_item
- current_weighted_average_cost_ex_vat
- current_inventory_quantity
- current_inventory_value_ex_vat
- current_purchase_price_ex_vat
- current_purchase_price_inc_vat
- created_at
- updated_at

## job_items

Planned fields:

- id
- job_id
- product_id
- description
- quantity
- unit_price
- vat_percent
- line_total
- created_at
- updated_at

Money calculation rule:

- Unit prices are entered and stored as VAT-inclusive prices in the MVP.
- `line_total` is `quantity * unit_price`, rounded to two decimals with decimal rounding.
- VAT breakdown is calculated from the VAT-inclusive line totals.
- `description`, `unit_price`, and `vat_percent` are snapshots on the work order item. Later product edits must not change historical work order rows.
- New databases use SQLAlchemy `Numeric` definitions for money-related columns.
- Existing SQLite databases created by earlier versions may still have older floating-point column affinity until a migration rebuilds those tables.
- A later migration should rebuild persisted money columns or move them to integer cents before serious multi-user financial use.

## receipts

Planned fields:

- id
- job_id
- receipt_number
- receipt_type
- printed_at
- editable_snapshot
- created_at

## settings

Planned fields:

- id
- key
- value
- updated_at

## audit_log

Planned fields:

- id
- event_type
- entity_type
- entity_id
- description
- created_at

## seller, cash, sales, and closing tables

The MVP now includes seller accounts, cash registers, shifts, sales, payments, refunds, cash movements, daily closings, and daily closing snapshots.

Important accounting rules:

- Work Orders, Sales, Payments, and Refunds are separate records.
- A Work Order is operational, not financial. It becomes billable by creating a Sale.
- A Sale may originate from direct POS sale or from a Work Order.
- A Sale may reference a Work Order, but payments are stored in `payments`.
- Work Order conversion is idempotent. A Work Order must not create multiple active Sales accidentally.
- Sales store `source_type`, `settlement_status`, `finalized_at`, and optional `invoice_customer_snapshot_json`.
- `settlement_status` tracks paid, partially paid, awaiting invoice, and partially paid awaiting invoice states.
- Invoice handoff creates no fake cash/card payment row. It is not statutory invoicing or accounting export.
- Split and partial payments are represented as multiple `payments` rows for the same Sale.
- `payments.received_by_user_id` is the operator who received or recorded payment; it is separate from the credited seller.
- `sales.sold_by_user_id` credits the seller for reports; `sales.created_by_user_id` records the operator.
- Refunds are stored in `refunds` and include `vat_breakdown_json`.
- Refunds reference the original sale through `sale_id`, but `shift_id`, `seller_id`, and `refunded_at` describe the actual refund event.
- A later refund is attributed to the refund shift business date and refunding seller. It does not move the original sale away from the original sale shift or seller.
- Daily closing stores immutable rows in `daily_closing_snapshots`.
- `daily_closings.current_version` points to the latest closing snapshot version.
- Reopening a Daily Closing must preserve older snapshots and store `reopened_at`, `reopened_by_user_id`, and `reopen_reason`.
- Closed business dates block financial writes until reopened.
- Daily closing reports use actual event dates: sales from sale shifts, refunds from refund shifts.
- SQLite compatibility creates partial unique indexes to prevent more than one open shift per seller and more than one open shift per cash register.

Current limitations:

- Authentication exists for local trusted-network use, but some MVP forms still preserve explicit user selectors for operational workflows.
- Role checks protect routes and business operations, but the app is still not hardened for public internet exposure.
- Sale documents, payment transaction numbers, refund numbers, shift numbers, and closing numbers are not official stable document numbers yet.
- Sales support multiple validated lines and multiple payment rows. Full accounting invoicing, external payment gateways, and statutory e-invoicing are not implemented.
- Multi-VAT refunds are rejected until line-level refund allocation is added.
- Financial refunds do not yet create customer-return inventory transactions.

Sales also store cost snapshots for stock-product sales:

- `sales.cost_of_goods_sold_ex_vat`
- `sales.gross_profit_ex_vat`
- `sales.gross_margin_percent`
- `sale_lines.cost_of_goods_sold_ex_vat`
- `sale_lines.gross_profit_ex_vat`
- `sale_lines.gross_margin_percent`

These values are snapshots from the weighted-average inventory cost at the time of sale. Later purchases or goods receipt reversals must not rewrite historical sale profitability.

## Inventory Ledger Tables

The inventory accounting backbone is `inventory_transactions`. It is the source of truth for stock quantity, inventory value, weighted average cost, purchase history, sale cost of goods sold, and audit reconstruction.

Important inventory rules:

- Inventory transactions are protected from normal application edits and SQLite `UPDATE`/`DELETE` operations. Corrections create new reversal or adjustment transactions.
- `quantity_change` stores the signed stock effect.
- `total_inventory_cost` stores the signed ex-VAT inventory value effect.
- `stock_before`, `stock_after`, `inventory_value_before`, `inventory_value_after`, `weighted_average_cost_before`, and `weighted_average_cost_after` preserve the running balance at the moment of the transaction.
- Goods receipt posting creates `purchase` transactions and includes allocated ex-VAT freight and other landed costs.
- Goods receipts also store freight and other-cost VAT rates, VAT amounts, and VAT-inclusive totals for purchase-document reconciliation. Deductible VAT is excluded from inventory value.
- Posted goods receipt cancellation creates reversal transactions and never deletes the original purchase history.
- Stock-product sales create `sale` transactions and store sale-line cost of goods sold and gross profit snapshots.
- Transfers create balanced transactions so total company inventory value remains unchanged.
- Negative stock is rejected by default.

Supporting tables:

- `suppliers`
- `warehouses`
- `warehouse_locations`
- `inventory_balances`
- `goods_receipts`
- `goods_receipt_lines`
- `inventory_transactions`

## Future tables

Later versions may add:

- attachments
- custom_fields
- custom_field_values
- backup_events

## Receipt numbering

Default format:

```text
YYYY-000001
```

Settings should allow:

- prefix
- yearly reset on/off
- next number
- number padding length

## Inventory value

Weighted-average inventory value is based on ex-VAT landed cost:

```text
new average cost =
(old inventory value + new landed receipt value)
/
(old quantity + received quantity)
```

Freight and other landed costs are allocated to receipt lines before weighted average cost is recalculated. If the same product appears on several receipt lines, product-level quantity, value, and average cost are calculated from all lines for that product, while ledger rows preserve deterministic line order by `goods_receipt_lines.id`. Ledger rows must always make the current stock and current inventory value reconstructable from history.

Cache fields such as `products.current_inventory_quantity`, `products.current_inventory_value_ex_vat`, `products.current_weighted_average_cost_ex_vat`, and `inventory_balances` are derived caches. The reconciliation report compares those caches to `inventory_transactions` with documented quantity, money, and cost tolerances. Repair rewrites caches from the ledger only; it never rewrites ledger rows.
