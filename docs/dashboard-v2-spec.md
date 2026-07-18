# Dashboard v2 UX Specification

## Goal

Turn the current dashboard into a daily operations workspace that answers three questions immediately:

1. What requires attention now?
2. What work is due or ready today?
3. What has happened recently?

The implementation must preserve the current local-first architecture, server-rendered FastAPI/Jinja workflows, mobile navigation improvements, auditability, and all existing document/sales history.

## Scope

This iteration covers:

- grouped navigation labels and destinations
- dashboard visual hierarchy
- critical task logic
- work queue summaries
- sales and invoicing summaries
- invoice queue tabs and invoice history
- user-friendly recent activity feed
- links to technical audit log
- responsive desktop/mobile behavior
- regression and workflow tests

This iteration does not replace the underlying audit log, delete financial records, or redesign every detail page.

## Navigation

Use four understandable groups.

### Sales and documents

- Cash Sale -> `/sales/quick`
- Quotes -> `/quotes`
- Delivery Notes -> `/delivery-notes`
- Work Orders -> `/work-orders`
- Invoicing -> `/sales/invoice-queue`

### Customers and stock

- Customers -> `/customers`
- Products -> `/products`
- Inventory Balances -> existing inventory balance route
- Goods Receipt -> existing receiving route

### Reports and history

- Sales Reports -> existing sales report route
- Inventory Reports -> existing inventory report route
- Invoice History -> `/sales/invoice-queue?view=all`

### Administration

- Language -> existing language control
- User Profile -> existing profile route
- Technical Audit Log -> `/audit-log`

Preserve the current collapsible mobile navigation behavior. On desktop, groups must be visually distinct without requiring unnecessary extra clicks for the most frequent actions.

## Dashboard layout

### 1. Page header

- Title: Dashboard / Työpöytä
- Optional concise date line
- Company status badge:
  - green: no current action required
  - yellow: attention required
  - red: overdue or unresolved critical issue
- The status badge links to the critical tasks section.

### 2. Quick actions

Place near the top, visible without scrolling on normal desktop viewport.

Primary actions:

- Create Work Order
- New Customer
- Cash Sale
- Create Quote

Use existing routes. Do not duplicate business logic.

### 3. Critical tasks

Show only meaningful alerts. Avoid using red for normal open-day state.

#### Daily closing states

- Neutral: current business day is open and closing is not yet expected
- Yellow: closing is expected soon but not completed
- Red: a previous business day remains unclosed, or another existing business rule marks closing overdue
- Green: closing completed

The implementation must rely on current daily closing records and business dates. Do not hard-code a misleading red warning throughout the entire day.

#### Overdue work orders

- 0: green or neutral state
- 1 or more: red alert card with count and link to filtered work orders

#### Invoice attention

Show a yellow/red item when invoices require action, for example:

- waiting to be transferred
- overdue/unpaid
- follow-up due

The exact count must be derived from existing invoice settlement statuses and follow-up data.

### 4. Work queues

Show separate summary cards:

- Overdue
- Due today
- Ready for pickup/delivery
- Upcoming

Each card links to the corresponding filtered list. Counts must only include work orders, not quotes or delivery notes, unless the card explicitly says otherwise.

Upcoming section:

- list up to 5-8 nearest work orders
- show date, customer, title, and status
- empty state: “No upcoming work orders”
- include Create Work Order button in empty state
- include View all link when results exist

### 5. Sales and invoicing

Show:

- today’s gross sales total
- optional refund total/net total only if already reliable in current domain logic
- open invoice attention count
- links to invoice views

Invoice view tabs:

- `action_required`: combined operational queue for records needing attention
- `waiting_transfer`: waiting to be transferred to invoicing
- `transferred`: transferred/external invoice created
- `unpaid`: unpaid/overdue/follow-up records
- `paid`: paid invoices
- `cancelled`: cancelled invoices
- `all`: complete invoice history

Default view should be `action_required`, not an undifferentiated all-record queue.

Every invoice/sale row should show, where available:

- sale/receipt number
- customer
- amount
- sold date
- due date
- settlement status
- external invoice number
- source work order/document link

No invoice or source document may disappear when marked paid, transferred, cancelled, or sold. Status changes must move records between views, not delete them.

## Document histories

Preserve and verify:

- Work Order History -> `/work-orders?view=history`
- Delivery Note History -> `/delivery-notes?view=history`
- Quote History -> `/quotes?view=history`
- Sales History -> `/sales`
- Invoice History -> `/sales/invoice-queue?view=all`

When a work order is converted to a sale:

- the work order remains stored
- it leaves the active list
- it appears in work order history
- the linked sale remains accessible

Where practical, display a lifecycle trail:

`Quote -> Delivery Note -> Work Order/Sale -> Invoice -> Paid`

Only show stages that exist. Do not invent relationships.

## Recent activity

Dashboard must show a user-friendly activity feed, not raw event codes.

Examples:

- Invoice #1042 marked paid
- Daily closing completed for 15 July 2026
- Sale #2034 created
- Work order #235 completed
- Customer “Example Oy” created

Requirements:

- retain raw audit events unchanged in the database
- map known event types to localized human-readable summaries
- link each item to its related record when possible
- show timestamp in a readable local format
- unknown event types use a safe generic fallback
- button: View technical audit log -> `/audit-log`

Implement formatting in a dedicated service/helper rather than large conditional blocks in the template.

## Responsive behavior

### Desktop

- quick actions and critical tasks visible high on the page
- cards use a clear hierarchy, not identical styling
- avoid excessive full-width whitespace

### Mobile

- preserve collapsible grouped navigation
- quick action buttons wrap or stack cleanly
- critical alerts appear before statistics
- no horizontal scrolling
- cards remain tappable with adequate spacing

## Accessibility and language

- do not rely on color alone; use labels/icons/text
- maintain Finnish and English translation keys
- use semantic headings
- buttons and links must have clear names
- preserve keyboard navigation

## Suggested implementation structure

Prefer a small dashboard/query service instead of adding more query logic directly to `app/main.py`.

Suggested modules:

- `app/services/dashboard_service.py`
- `app/services/activity_feed_service.py`
- invoice filtering helpers in the existing sales service or a dedicated invoice query service

Likely templates/files:

- `app/templates/dashboard.html`
- `app/templates/base.html`
- `app/templates/sales/invoice_queue.html`
- translation files/service
- dashboard and sales routes
- CSS/static assets

Do not create a second competing dashboard route.

## Testing requirements

Add focused tests for:

1. work order converted to sale remains in work order history
2. quote and delivery note histories remain separate
3. paid invoice appears in paid and all views, not action-required
4. transferred invoice appears in transferred and all views
5. cancelled invoice appears in cancelled and all views
6. unpaid/overdue invoice appears in action-required and unpaid
7. dashboard daily closing card uses neutral/yellow/red/green states correctly
8. overdue work order count and links
9. user-friendly audit event formatting and unknown-event fallback
10. Finnish and English dashboard rendering
11. mobile navigation structure remains present
12. existing full test suite remains green

## Acceptance criteria

- A user understands the day’s operational state without opening multiple pages.
- Important unresolved items are visually prominent.
- Normal states do not produce false red alarms.
- All histories remain available and searchable.
- Invoice queue and invoice history are clearly separated by filters/tabs.
- Raw audit data remains available to administrators.
- Dashboard activity is understandable to a non-technical user.
- Existing workflows and tests continue to work.
- No records are deleted merely because their state changes.
