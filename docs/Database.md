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
- A Sale may reference a Work Order, but payments are stored in `payments`.
- Refunds are stored in `refunds` and include `vat_breakdown_json`.
- Daily closing stores immutable rows in `daily_closing_snapshots`.
- `daily_closings.current_version` points to the latest closing snapshot version.
- Reopening a Daily Closing must preserve older snapshots and store `reopened_at`, `reopened_by_user_id`, and `reopen_reason`.
- Closed business dates block financial writes until reopened.
- SQLite compatibility creates partial unique indexes to prevent more than one open shift per seller and more than one open shift per cash register.

Current limitations:

- User IDs are selected from forms; there is no authenticated current user yet.
- Sale documents, payment transaction numbers, refund numbers, shift numbers, and closing numbers are not official stable document numbers yet.
- Sales UI creates one line and one payment. Future versions should finalize sales from multiple validated lines and separate payment balancing.
- Multi-VAT refunds are rejected until line-level refund allocation is added.

## Future tables

Later versions may add:

- inventory_items
- inventory_events
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

Inventory value formula:

```text
stock_balance * purchase_price = inventory_value
```

Inventory events should be added later to avoid losing history when stock changes.
