# Local-First Operations Tracker

A configurable local-first work order and operations tracker for small businesses.

## Portfolio Summary

This project demonstrates a pragmatic FastAPI business application built around real small-business workflows: work orders, customer history, product pricing, receipts, seller shifts, sales, refunds, cash handling, daily closing, immutable financial snapshots, backups, and bilingual Finnish/English UI support.

The goal is not to imitate a SaaS landing page. The app focuses on operational correctness, auditability, local-first use, and maintainable server-rendered workflows that can run on a company-owned computer and be accessed from nearby devices.

## UI Preview

The current dashboard is designed as an operations view rather than a marketing page. It shows daily work order pressure, shift status, daily closing state, recent activity, and upcoming work in one browser screen.

More UI screenshots are available in [`docs/UI/Screenshots.md`](docs/UI/Screenshots.md).

### Browser Dashboard

![Browser dashboard](docs/UI/screenshots/dashboard-desktop.png)

### Mobile Dashboard

The mobile layout uses the same server-rendered UI with a single-column dashboard suitable for phone use over LAN or Tailscale.

![Mobile dashboard](docs/UI/screenshots/dashboard-mobile.png)

## Current MVP Status

This repository contains an early but usable FastAPI MVP. It is intended to run on one company-owned Windows computer and serve other computers, tablets, and phones through a browser on the local network or through Tailscale.

The app is not intended to be exposed directly to the public internet.

## Implemented Features

- Dashboard with real work order counts and attention lists
- Customer CRUD and customer work order history
- Work Order CRUD through `/work-orders`
- Legacy `/jobs` routes kept for backwards compatibility
- Configurable work order statuses in Settings
- Products and services with CSV price list import
- Work order item rows with VAT-inclusive pricing
- Sequential receipt numbers independent from database IDs
- Printable receipt / work order preview with stored print snapshot
- Settings for company details, VAT default, receipt prefix, and language
- Finnish and English UI text baseline
- Local login with signed session cookie, first-admin setup, password hashes, and operational roles for Admin, Manager, Seller, and Read only
- Cash registers and seller shifts with starting cash, cash movements, closing count, expected cash, and over/short calculation
- Sales, payments, and refunds stored separately from Work Orders
- Daily closing with immutable versioned snapshots, closed-day write lock, VAT/payment/seller summaries, and authorized reopen flow
- Read-only browsing for historical daily closing snapshot versions
- Seller reports for daily, weekly, and monthly sales metrics
- Sales report totals
- Audit log
- SQLite backups using SQLite's backup API
- Backup restore, health status, and retention cleanup
- Automatic background backup scheduler with configurable interval and retention
- Alembic baseline migration for the current schema
- Centralized HTML and JSON error handling
- Dockerfile and Docker Compose support for the SQLite local-first deployment
- GitHub Actions pytest workflow for push and pull request checks
- LAN/Tailscale run script support

## Known Limitations

- Authentication is local-session based and intended for a trusted company network; it is not hardened for public internet exposure.
- Non-development startup fails if `SECRET_KEY` is missing or still uses a known development default.
- State-changing authenticated HTML forms use CSRF tokens backed by signed local cookies.
- Some operational forms still preserve seller/admin selectors for MVP workflows. Critical financial routes now derive the effective seller or closing user from the active session where authentication is configured, but this is still not a substitute for public-internet-grade authorization.
- No cloud deployment, PostgreSQL, or object storage
- No native mobile application
- Backup scheduler is in-process and intended for the local single-computer deployment model; use an external scheduler for stricter production guarantees
- Alembic has a baseline migration for new databases. Docker startup runs `alembic upgrade head`; the Windows app startup still creates missing tables and applies a small compatibility shim, so older unknown schemas should be upgraded deliberately and backed up first.
- Receipt numbering is local-MVP safe, but not designed for high-concurrency multi-server use
- Money columns now use SQLAlchemy `Numeric`; existing SQLite columns may still have older storage affinity until a future migration rebuilds the tables
- Bootstrap CSS and JavaScript are bundled locally under `app/static/vendor/bootstrap`; the app does not require a CDN for the normal UI
- Sales UI creates one sale line and one payment today. The data model is prepared for more rows, but split/partial payments and multi-line sale finalization are not yet implemented.
- Multi-VAT refunds are rejected until line-level refund allocation is implemented.

## Sales, Shifts, Refunds, And Daily Closing

Work Orders, Sales, Payments, and Refunds are separate business objects. A Sale may link to a Work Order, but a Work Order is not treated as the payment record.

Daily closing rules:

- All shifts for the business date must be closed before the day can be closed.
- Closing creates a stored immutable snapshot with a version number.
- A closed business date blocks new shifts, sales, refunds, cash movements, and shift closing for that date.
- Only reopening the Daily Closing unlocks that date.
- Re-closing after reopen creates a new snapshot version and preserves older snapshot rows.
- Refunds cannot exceed the original sale total cumulatively.
- Refunds are recorded on the current open refund shift and the refunding seller, not on the original sale shift.
- The original sale remains on its original sale date and seller. Later refunds reduce the refund day and refunding seller totals.
- Refund VAT is stored with the refund. Single-VAT sales are supported; multi-VAT refunds require future line allocation.
- Snapshot version history is available from the Daily Closing detail page.

Security notes:

- Create the first admin at `/setup`, then use `/login`.
- Passwords are stored as PBKDF2-SHA256 hashes.
- Signed HTTP-only session cookies are used for local browser sessions. Cookie `Secure`, `SameSite`, and max age are configurable through environment variables.
- Authenticated write forms use CSRF tokens. Login/setup remain intentionally simple local setup flows; logout and protected write routes require CSRF once authentication is configured.
- Repeated failed logins are temporarily throttled and audited without storing passwords in logs.
- Admin and Manager roles can access administration routes. Read only users cannot perform write requests.
- The app is still not intended to be exposed directly to the public internet.

## Technology Stack

- Python
- FastAPI
- SQLite
- SQLAlchemy classic Column models
- Jinja2
- Bootstrap
- Pytest
- Uvicorn

## Quick Start On Windows

Clone the repository:

```powershell
git clone https://github.com/denzo69/Local-First-Operations-Tracker.git
cd Local-First-Operations-Tracker
```

Start the local development server:

```powershell
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

Health check:

```text
http://127.0.0.1:8000/health
```

## Docker

Docker is optional. The compose setup runs the app with SQLite stored in a named volume and backups stored in a separate named volume.

```powershell
set SECRET_KEY=replace-with-a-long-random-value
docker compose up --build
```

Then open:

```text
http://127.0.0.1:8000
```

Docker Compose requires `SECRET_KEY` from the environment and refuses to start without it. The Docker setup intentionally keeps the current local-first SQLite model; PostgreSQL and object storage are not enabled yet. The container runs Alembic migrations before starting Uvicorn and includes a `/health` healthcheck.

Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
```

Create or upgrade a database through Alembic:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
```

Optional backup scheduler environment settings:

```text
BACKUP_SCHEDULER_ENABLED=true
BACKUP_SCHEDULER_INTERVAL_MINUTES=1440
BACKUP_RETENTION_COUNT=50
SESSION_COOKIE_SECURE=false
SESSION_COOKIE_SAMESITE=lax
SESSION_MAX_AGE_SECONDS=43200
LOGIN_THROTTLE_MAX_ATTEMPTS=5
LOGIN_THROTTLE_WINDOW_SECONDS=300
```

## Local Network And Tailscale Access

Use the LAN script when another device should access the app:

```powershell
.\run-lan.bat
```

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

## Print Snapshots

Opening the printable receipt / work order route creates one stored snapshot for that document type. Later edits to the live work order do not rewrite the stored snapshot. Reopening the same printable route reuses the existing document number and snapshot.

## Documentation

Design documents live in `docs/`:

- `docs/Vision.md`
- `docs/Projektisuunnitelma_v1.md`
- `docs/Software_Design_Document.md`
- `docs/Architecture.md`
- `docs/Backup_and_Failover.md`
- `docs/Database.md`
- `docs/API.md`
- `docs/Roadmap.md`
- `docs/UI/Wireframes.md`
