# JEronAI Operations

**A local-first ERP and CRM portfolio project for small-business operations.**

JEronAI Operations is a browser-based business application built with FastAPI, SQLite, SQLAlchemy, Jinja2, and Bootstrap. It demonstrates how customer management, operational documents, sales, stock control, reporting, audit history, and backups can be combined into one maintainable local-first system.

This is an actively developed portfolio and product-development project. It is not presented as a finished accounting suite, certified cash register, or statutory e-invoicing product.

## What The Project Demonstrates

- Local-first deployment on company-owned hardware
- Responsive browser use from desktop, tablet, and phone
- Customer, product, service, warehouse, and supplier registers
- Work orders, quotes, and delivery notes with conversion workflows
- Direct sales and document-based sales
- Cash, card, split-payment, and external-invoicing handoff workflows
- Daily closing, refunds, seller reporting, VAT summaries, and audit history
- Goods receiving, stock balances, inventory transactions, weighted-average costing, and inventory valuation
- Local authentication, operational roles, database migrations, backups, and automated tests
- Finnish and English user-interface support

## UI Preview

The interface is designed around everyday operational work rather than a marketing-style dashboard. Desktop and mobile views use the same server-rendered application, so a company can run the system on one computer and use it from other trusted devices through a local network or Tailscale.

### Desktop Dashboard

![Desktop dashboard](docs/UI/screenshots/dashboard-desktop.png)

### Mobile Navigation

The mobile navigation groups sales and documents, customers and stock, reports and history, and administration into collapsible sections.

![Mobile navigation](docs/UI/screenshots/dashboard-mobile.png)

## Current Status

The repository contains an early but usable FastAPI MVP and portfolio implementation. It is intended for a trusted private environment:

- one company-owned Windows computer or Docker host
- access from computers, tablets, and phones through a browser
- local network or Tailscale access
- SQLite as the local database
- no direct public-internet exposure

## Implemented Features

### Customers And Operational Documents

- Customer CRUD, contact details, company details, notes, and document history
- Work Orders through `/work-orders`
- Quotes through `/quotes`
- Delivery Notes through `/delivery-notes`
- Configurable document statuses
- Conversions between Work Orders, Quotes, Delivery Notes, Sales, and external-invoicing handoff
- Printable document views with stored snapshots
- Guards against deleting referenced operational documents

Quotes are non-financial and do not reduce inventory. Delivery-note and sale inventory behavior follows the application’s current inventory transaction rules; the financial Sale remains a separate business object.

### Products And Inventory

- Product and service register
- CSV product import with common price and VAT column aliases
- Warehouses and shelf locations
- Suppliers and goods receipts
- Draft and posted goods-receipt workflow
- Freight and additional landed-cost allocation
- Stock balances and immutable inventory transaction history
- Weighted-average inventory cost
- Ex-VAT inventory valuation
- Inventory reconciliation and cache repair
- Reversal transactions instead of deleting posted inventory history
- Cost-of-goods-sold and gross-profit snapshots on finalized sales

### Sales, Payments, And Invoicing Follow-Up

- Direct quick sale
- Sales created from operational documents
- Multiple sale lines
- Cash, card, bank transfer, mobile, other, and split-payment records
- Refund records stored separately from the original sale
- Sequential sale document numbers
- External-invoicing handoff and follow-up queue
- External invoice number, due date, follow-up date, notes, and payment-status confirmation
- Paid, partially paid, awaiting invoice, transferred, unpaid, reminder, and cancelled states

The invoice queue is a manual handoff and follow-up workflow. It is **not** statutory invoicing, e-invoicing, accounting integration, or a payment gateway.

### Daily Operations And Reporting

- Operations dashboard with work queues and attention states
- Optional cash registers and seller shifts
- Starting cash, cash movements, expected cash, and over/short calculation
- Daily closing with stored versioned snapshots
- Closed-date write protection and authorized reopen flow
- VAT, payment-method, seller, refund, and sales summaries
- Seller reports, sales reports, gross-profit metrics, and inventory valuation
- Audit log with operator and entity references

### Security, Reliability, And Deployment

- First-admin setup and local login
- PBKDF2-SHA256 password hashes
- Signed HTTP-only session cookies
- Admin, Manager, Seller, and Read only roles
- SQLite backups and restore workflow
- Backup integrity checks and retention cleanup
- Background backup scheduler
- Alembic migration bootstrap for new and legacy SQLite databases
- Centralized HTML and JSON error handling
- Windows run scripts
- Dockerfile and Docker Compose
- GitHub Actions and Pytest validation
- LAN and Tailscale support

## Known Limitations

- Intended for a trusted private network, not direct public-internet exposure
- No statutory accounting, fiscal cash-register certification, payment gateway, or e-invoice integration
- No native mobile app; mobile use is through the responsive browser interface
- SQLite and local-session architecture are designed for a single-server local-first installation, not high-concurrency multi-server SaaS
- Refunds do not yet create customer-return stock movements
- Multi-VAT refunds require future line-level allocation
- The in-process backup scheduler is appropriate for the local deployment model; stricter production environments should also use an external scheduler
- Some usability and responsive-layout details remain under active development

## Technology Stack

- Python
- FastAPI
- SQLite
- SQLAlchemy
- Alembic
- Jinja2
- Bootstrap
- Pytest
- Uvicorn

## Quick Start On Windows

```powershell
git clone https://github.com/denzo69/-Local-First-Operations-Tracker-Commercial.git
cd -Local-First-Operations-Tracker-Commercial
.\run.bat
```

Open:

```text
http://127.0.0.1:8000
```

Create the first admin account:

```text
http://127.0.0.1:8000/setup
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

## Local Network And Tailscale

```powershell
.\run-lan.bat
```

Open the server computer’s private LAN or Tailscale address in a browser. Do not port-forward the development server to the public internet.

## Data And Backups

Default database:

```text
data/app.sqlite
```

Default backup directory:

```text
backups/
```

Backups use SQLite’s backup API and are checked before being offered for restore. Restoring creates a safety backup before replacing the active database.

## Documentation

Additional design, architecture, database, backup, API, roadmap, and UI documentation is available under [`docs/`](docs/).
