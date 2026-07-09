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

## Entity overview

```text
Customer 1---N Job
Job 1---N JobItem
Product 1---N JobItem
Job 1---N Receipt
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

## Future tables

Later versions may add:

- inventory_items
- inventory_events
- users
- roles
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
