# JEronAI Operations

**A local-first ERP and CRM portfolio project for small-business operations.**

JEronAI Operations is a browser-based business application built with FastAPI, SQLite, SQLAlchemy, Jinja2, and Bootstrap.

It demonstrates how customer management, operational documents, sales, inventory control, daily closing, reporting, audit history, and backups can be combined into one maintainable local-first system.

> This is an actively developed portfolio and product-development project. It is an early but usable MVP, not a finished accounting suite, certified cash register, payment platform, statutory invoicing product, or e-invoicing product.

## Highlights

- Local-first deployment on company-owned hardware
- Responsive browser interface for desktop, tablet, and phone
- Customer, product, service, warehouse, supplier, cash register, and user registers
- Work Orders, Quotes, Delivery Notes, and document conversion workflows
- Direct Quick Sale and document-based Sale workflows
- Cash, card, bank transfer, mobile, other payment, split-payment, refund, and external Invoice Handoff workflows
- Invoice Follow-up for manual payment checks, unpaid status, reminder tracking, and paid confirmation
- Daily Closing with stored historical snapshots and closed-date write locks
- Goods Receipts, Stock Balances, Inventory Transactions, weighted-average costing, and Inventory Valuation
- Product CSV import with update-by-name behavior
- Reporting, Audit Log, database migrations, Backups, and Restore
- Local authentication and operational user roles
- Finnish and English user-interface support
- Automated pytest suite with current configured application-code coverage at 100%

## UI Preview

The screenshots below use the current JEronAI Operations visual design and the existing image files in this repository.

### Desktop Dashboard

![Desktop dashboard](docs/UI/screenshots/dashboard-desktop.png)

### Mobile Dashboard

![Mobile dashboard](docs/UI/screenshots/dashboard-mobile.png)

## Current Status

JEronAI Operations currently runs as a local-first FastAPI application with SQLite as the local database. It is designed for trusted private environments, such as one company-owned Windows computer or Docker host serving nearby computers, tablets, and phones through a browser.

LAN and Tailscale access are supported for private-network use. The app is not intended for direct public-internet exposure.

The mobile experience is a responsive browser UI, not a native mobile application.

## Implemented Features

### Customer, Product, And User Registers

- Customer CRUD and customer Work Order history
- Customer default discount percentage for future Sales when the customer is selected from the register
- Product and service records with VAT-inclusive sales pricing
- Stock products and non-stock/service products
- Product workspace for product master data, stock receiving, warehouses, shelf locations, stock balances, inventory history, Inventory Valuation, and reconciliation
- Supplier records for Goods Receipts, including manual supplier entry during receiving
- User management, first-admin setup, local authentication, password hashes, and Admin, Manager, Seller, and Read only roles
- Settings for company details, default VAT percent, receipt prefix, language, dashboard visibility, and workflow statuses

### Operational Documents

- Work Orders through `/work-orders`
- Quotes through `/quotes`
- Delivery Notes through `/delivery-notes`
- Legacy `/jobs` routes retained for backwards compatibility
- Configurable operational statuses
- Stored printable document snapshots and stable document numbers

### Sales, Payments, And Reporting

- Unified Sales service for direct Quick Sale and document-based Sales
- Sale document numbers shared by Quick Sale and Work Order-originated Sales
- Cash, card, bank transfer, mobile, other, split-payment, partial-payment, and external Invoice Handoff settlement paths
- Payment rows stored separately from Sale rows
- Refund rows stored separately from Sale and Payment rows
- Seller reports for daily, weekly, and monthly sales and margin metrics
- Sales reports and printable seller commission-style summaries
- Customer-facing receipt output separate from internal Sale detail and audit views

### Inventory And Costing

- Goods Receipt draft and posting workflow
- Warehouse and shelf-location records
- Stock Balance views by product and location
- Inventory Transaction ledger with application guards and SQLite update/delete triggers
- Weighted-average inventory cost based on ex-VAT landed cost
- Freight and other landed-cost allocation by purchase value or quantity
- Inventory Valuation and ledger/cache reconciliation
- Sale-line cost-of-goods-sold and gross-profit snapshots using the weighted average cost at Sale time

### Operations, Backups, And Auditability

- Dashboard with quick actions, work queues, upcoming Work Orders, recent activity, sales/invoicing summary, and Daily Closing status
- Optional Cash Registers and Seller Shifts for businesses that need cashier balancing
- Daily Closing with immutable versioned snapshots and authorized reopen flow
- Audit Log for operational events
- SQLite backups through SQLite's backup API
- Backup Restore with safety backup before replacement
- In-process backup scheduler for the local single-computer deployment model
- Safe Alembic migration bootstrap for new and legacy unstamped SQLite databases
- Centralized HTML and JSON error handling
- Dockerfile, Docker Compose, Windows run scripts, and GitHub Actions pytest workflow

## Known Limitations

- This is an early MVP and portfolio/product-development project.
- Authentication is local-session based and intended for trusted private environments.
- The app is not hardened for direct public-internet exposure.
- The app is not a certified fiscal cash register.
- The app does not include payment gateway processing.
- The Invoice Queue is an external invoicing handoff and follow-up workflow only.
- The app does not create statutory invoices or e-invoices.
- PostgreSQL and cloud object storage are not currently implemented.
- SQLite is the current local database.
- There is no native mobile application.
- Receipt numbering is local-MVP safe, but not designed for high-concurrency multi-server use.
- Backup scheduling is in-process; use an external scheduler for stricter operational guarantees.
- Multi-VAT refunds are rejected until line-level refund allocation is implemented.
- Financial refunds do not yet create customer-return stock movements. A refund leaves inventory unchanged until a dedicated return workflow is implemented.
- Bootstrap CSS and JavaScript are bundled locally under `app/static/vendor/bootstrap`; the normal UI does not require a CDN.

## Detailed Sales And Document Workflows

Work Orders, Quotes, Delivery Notes, Sales, Payments, and Refunds are separate business objects.

A Work Order is operational, not financial. When it becomes billable, it is converted into a Sale. The Sale stores immutable line snapshots, credited seller, authenticated operator, cash register or shift reference when used, VAT totals, cost-of-goods-sold snapshots, and settlement status.

A Quote is used to price products, services, or work without reducing inventory.

A Delivery Note represents products reserved, delivered, or prepared for a customer before final settlement. Delivery Notes reduce stock products when issued. Service rows and manual work rows do not reduce stock. When a Delivery Note is later converted to a Sale, the existing Delivery Note stock issue is reused for Sale cost reporting and stock is not reduced a second time.

Direct Quick Sale and Work Order billing use the same Sale engine:

- `/sales/quick` for direct POS-style Sale
- `/sales/work-orders/{id}` for Work Order review and settlement
- `/sales/invoice-queue` for external invoicing handoff and follow-up

Settlement and follow-up states include paid, partially paid, awaiting invoice, transferred to invoicing, payment check due, unpaid, reminder due, reminder sent, and cancelled. Sending a Sale to external invoicing does not create a fake cash/card payment.

External Invoice Handoff can store the external service name, external invoice number, invoice date, due date, optional external reference or URL, and notes. The app never assumes payment has happened without explicit user confirmation.

Every finalized Sale receives one unique sequential Sale document number. Work Order numbers remain source references. External invoice numbers remain separate handoff fields and never replace the Sale document number.

Seller and operator identity remain separate:

- `Sale.sold_by_user_id` credits the seller for receipts and Seller Reports
- `Sale.created_by_user_id` records the authenticated operator who created the Sale
- `Payment.received_by_user_id` records who received or recorded each payment
- `InventoryTransaction.created_by_user_id` records who caused the stock movement

Cashier shifts are optional by default. When a shift is selected, the Sale uses the shift business date and cash register and appears in shift balancing. When no shift is selected, the Sale stores its own business date, can optionally reference a cash register, and appears in Sales Reports, Daily Closing totals, Inventory Reports, and Seller Reports without appearing in a shift closing.

Daily Closing rules:

- All shifts for the business date must be closed before that date can be closed.
- Closing creates a stored versioned snapshot.
- A closed business date blocks new shifts, Sales, Refunds, cash movements, and shift closing for that date.
- Reopening the Daily Closing unlocks that business date for authorized users.
- Re-closing after reopen creates a new snapshot version and preserves older snapshot rows.
- Refunds cannot exceed the original Sale total cumulatively.
- Later Refunds reduce the refund day and refunding seller totals; the original Sale remains on its original Sale date and credited seller.

## Inventory Costing

The visible inventory workflow is organized under `Products / Tuotteet`. Product master data, suppliers, warehouses, shelf locations, Goods Receipts, Stock Balances, Inventory Transactions, Inventory Valuation, and reconciliation are presented as one product and inventory workspace. Internal services and tables remain separated for correctness.

Inventory valuation is based on ex-VAT cost. VAT is stored and shown, but deductible VAT is not included in inventory value by default.

Goods Receipts are created as drafts. Draft receipts do not affect stock, balances, weighted average cost, or valuation. Posting a receipt allocates freight and other landed costs, creates immutable Inventory Transactions, updates location balance caches, updates product-level cache totals, and writes audit events in one transaction.

Receipt-level freight and other landed costs store both ex-VAT and VAT amounts. Purchase-document VAT totals include product-line VAT, freight VAT, and other-cost VAT.

Default landed-cost allocation is by purchase value:

```text
line share = line purchase value / total receipt purchase value
allocated freight = receipt freight total * line share
allocated other costs = receipt other costs total * line share
landed unit cost = (line purchase value + allocated freight + allocated other costs) / quantity
```

Quantity-based allocation is also supported. Monetary allocations are rounded to 2 decimals and the final rounding remainder is assigned deterministically so allocated totals reconcile to the receipt totals.

If the same stock product appears on multiple receipt lines, posting processes rows in `GoodsReceiptLine.id` order for traceable ledger rows, while product-level projected quantity, value, and weighted average are reconciled from all lines for that product.

Weighted average cost uses 6 decimal places internally:

```text
old value = old quantity * old weighted average cost
new receipt value = received quantity * landed unit cost
new average cost = (old value + new receipt value) / (old quantity + received quantity)
```

The Inventory Transaction ledger is the accounting source of truth for stock quantity and value. Current balance caches and product-level cost fields must be reproducible from ledger rows. Negative stock is rejected by default because it would make weighted-average cost ambiguous.

Sale-line COGS and gross-profit snapshots use the weighted average cost that existed when the Sale was finalized. Later purchases do not rewrite historical profit. Non-stock products and services have zero inventory COGS in the current MVP cost model.

## Technology Stack

- Python
- FastAPI
- SQLite
- SQLAlchemy classic Column models
- Alembic
- Jinja2
- Bootstrap
- Pytest
- Uvicorn
- Docker and Docker Compose

## Quick Start On Windows

Clone the current repository:

```powershell
git clone https://github.com/denzo69/-Local-First-Operations-Tracker-Commercial.git
cd -Local-First-Operations-Tracker-Commercial
```

Start the local development server:

```powershell
.\run.bat
```

The run script installs requirements and applies the safe migration bootstrap before starting Uvicorn.

Open:

```text
http://127.0.0.1:8000
```

Create the first admin account:

```text
http://127.0.0.1:8000/setup
```

Health check:

```text
http://127.0.0.1:8000/health
```

## Docker

Docker is optional. The compose setup runs the app with SQLite stored in a named volume and backups stored in a separate named volume.

```powershell
docker compose up --build
```

Then open:

```text
http://127.0.0.1:8000
```

Before real use, change `SECRET_KEY` in `docker-compose.yml` or provide it through your environment. The Docker setup intentionally keeps the current local-first SQLite model; PostgreSQL and object storage are not enabled yet.

## Tests

Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
```

Run the configured coverage check:

```powershell
.\.venv\Scripts\python.exe -m pytest -q --durations=20 --cov=app --cov-report=term-missing --cov-report=xml --cov-report=html
```

The current automated test suite reports 100% application-code coverage under the configured local test matrix. This does not guarantee the absence of defects; it means the current measured application code paths are covered by automated tests.

GitHub Actions runs the pytest workflow on pushes and pull requests to `main`.

## Local Network And Tailscale Access

Use the LAN script when another device should access the app:

```powershell
.\run-lan.bat
```

The LAN script applies the safe migration bootstrap before binding to `0.0.0.0`.

Then open the server computer's LAN or Tailscale address in a browser, for example:

```text
http://100.x.x.x:8002
```

Only use this on trusted private networks or Tailscale. Do not port-forward the development server to the public internet.

## Data And Backups

Default local database:

```text
data/app.sqlite
```

Default backup folder:

```text
backups/
```

Backups are created with SQLite's backup API, validated with `PRAGMA integrity_check`, and listed in the Backups page. Restore creates a safety backup before replacing the current database.

Optional backup scheduler environment settings:

```text
BACKUP_SCHEDULER_ENABLED=true
BACKUP_SCHEDULER_INTERVAL_MINUTES=1440
BACKUP_RETENTION_COUNT=50
```

## Database Migration Safety

Create or upgrade a database through the safe migration bootstrap:

```powershell
.\.venv\Scripts\python.exe -m app.migration_bootstrap
```

Preview the migration decision without changing the database:

```powershell
.\.venv\Scripts\python.exe -m app.migration_bootstrap --dry-run
```

Earlier local-first builds could create SQLite tables before Alembic version stamping existed. If such a database has application tables but no `alembic_version` row, a raw `alembic upgrade head` can fail because the baseline migration tries to recreate existing tables.

Use `python -m app.migration_bootstrap` instead. It classifies unstamped SQLite schemas and stamps only when the schema satisfies the critical checks for a known revision. Unknown or partial schemas abort without stamping or upgrading.

Before modifying an existing non-empty SQLite database, the bootstrap creates a migration backup with SQLite's backup API under `backups/migration-backups/` and verifies it with `PRAGMA quick_check`.

Do not casually run `alembic stamp head`. Stamping head is safe only after the full current schema has been confirmed.

## Print Snapshots

Opening a printable customer receipt or operational document route creates or reuses a stored print snapshot. Later edits to the live Work Order do not rewrite an existing stored snapshot. Reopening the same printable route reuses the existing document number and snapshot.

Customer receipts are intentionally separate from internal Sale detail views. Customer receipts hide audit details, internal IDs, inventory cost, gross profit, and internal settlement diagnostics.

## CSV Import

Product CSV import supports UTF-8 CSV files with case-insensitive, whitespace-normalized headers. Existing products are updated by matching the `name` column.

Supported price columns:

- `unit_price`
- `price`
- `price_eur`
- `selling_price`
- `sales_price`
- `unitprice`

Supported VAT columns:

- `vat_percent`
- `vat`
- `alv`
- `alv_percent`

If no VAT column is provided for a row, the application default VAT percent is used. Empty files, invalid encodings, undetectable delimiters, missing headers, and CSV files without a `name` column are rejected with validation errors.

## Documentation

Design and technical documents live in `docs/`:

- [Vision](docs/Vision.md)
- [Project plan](docs/Projektisuunnitelma_v1.md)
- [Software Design Document](docs/Software_Design_Document.md)
- [Architecture](docs/Architecture.md)
- [Backup and Failover](docs/Backup_and_Failover.md)
- [Database](docs/Database.md)
- [API](docs/API.md)
- [Roadmap](docs/Roadmap.md)
- [Tailscale Remote Access](docs/Tailscale_Remote_Access.md)
- [UI Wireframes](docs/UI/Wireframes.md)
- [UI Screenshots](docs/UI/Screenshots.md)
- [Unified Sales Screenshots](docs/UI/UnifiedSalesScreenshots.md)

## License

This repository is source-available for portfolio and evaluation purposes but is proprietary software. No permission is granted to use, copy, modify, distribute, host, sell, or create derivative works without a separate written license agreement from the copyright holder.

See [LICENSE](LICENSE) for the complete terms.
