# Local-First Operations Tracker

A configurable local-first work order and operations tracker for small businesses.

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
- Sales report totals
- Audit log
- SQLite backups using SQLite's backup API
- Backup restore, health status, and retention cleanup
- GitHub Actions pytest workflow for push and pull request checks
- LAN/Tailscale run script support

## Known Limitations

- No authentication or user permissions yet
- No cloud deployment, Docker, PostgreSQL, or object storage
- No native mobile application
- No automatic background backup scheduler yet
- Existing SQLite databases are not migrated automatically when model definitions change
- Receipt numbering is local-MVP safe, but not designed for high-concurrency multi-server use
- Money columns now use SQLAlchemy `Numeric`; existing SQLite columns may still have older storage affinity until a future migration rebuilds the tables
- Bootstrap is still loaded from CDN in the normal UI; print views use local print CSS

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

Health check:

```text
http://127.0.0.1:8000/health
```

Run the full test suite:

```powershell
.\.venv\Scripts\python.exe -m pytest
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
