# Local-First Operations Tracker

A local-first work order, sales, cash-shift, and daily-closing system for small businesses.

The app solves a common small-business operations problem: work orders, customer history, price rows, sales, cash reconciliation, refunds, receipts, and daily closing are often scattered across notebooks, spreadsheets, and payment terminals. This MVP brings those workflows into one browser-based system that runs on a company-owned Windows computer and can be used over LAN or Tailscale.

## Highlights

- Customer and Work Order CRUD with attention lists for overdue, due, and ready work
- Product/service catalog with CSV price-list import and VAT-inclusive pricing
- Sequential receipt numbers independent from raw database IDs
- Seller accounts, cash registers, cash shifts, cash movements, and over/short calculation
- Sales, payments, and refunds stored separately from Work Orders
- Immutable daily closing snapshots with version history and closed-day write locking
- Finnish and English UI baseline with persistent language setting
- SQLite backup, restore, retention, and health checks using SQLite's backup API

## Architecture

- Server-rendered FastAPI and Jinja2 application
- SQLite local database for the MVP deployment model
- SQLAlchemy models with service-layer business rules
- Bootstrap served from local static files under `/static/vendor/bootstrap/`
- Local-first Windows/LAN/Tailscale positioning; no cloud dependency required

See [docs/Architecture.md](docs/Architecture.md) and [docs/Database.md](docs/Database.md).

## Current Status

MVP maturity: early but usable for demonstration and local workflow validation. The accounting-related workflows have guardrails for daily closing snapshots, closed-day locks, refund limits, and cash-shift reconciliation, but this is not production-secure yet.

Verified locally:

- Python 3.14.6
- Tests: 85 passed, 0 failed, 0 skipped, 407 warnings
- Coverage baseline: 85% measured with `pytest-cov`

Security limitation: there is no real authentication/session/current-user system yet. Current role checks are business-rule validation, not secure identity verification.

## Quick Start

```powershell
git clone https://github.com/denzo69/Local-First-Operations-Tracker.git
cd Local-First-Operations-Tracker
.\run.bat
```

Open:

```text
http://127.0.0.1:8000
```

Run tests:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Run tests with coverage:

```powershell
.\.venv\Scripts\python.exe -m pytest --cov=app --cov-report=term-missing --cov-report=xml --cov-report=html
```

## Screenshots

No repository screenshots are currently included. This section intentionally avoids broken image links.

## Documentation

- [Operations](docs/Operations.md)
- [Backups](docs/Backups.md)
- [Security](docs/Security.md)
- [Development](docs/Development.md)
- [Architecture](docs/Architecture.md)
- [Database](docs/Database.md)
- [API](docs/API.md)
- [Roadmap](docs/Roadmap.md)
- [Vision](docs/Vision.md)

## Known Limitations

- No authentication, sessions, CSRF protection, or secure current-user enforcement yet
- No Alembic migrations, Docker, PostgreSQL, cloud deployment, or native mobile app
- Seller/admin IDs are still selected from forms in operational flows
- Multi-VAT refunds are blocked until line-level refund allocation exists
- Sales UI currently creates one sale line and one payment
- Receipt numbering is local-MVP safe, but not designed for high-concurrency multi-server deployment
