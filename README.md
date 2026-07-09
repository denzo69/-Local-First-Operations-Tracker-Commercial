# Local-First Operations Tracker

A configurable local-first operations tracker for small businesses.

## Project status

**Status:** Design phase / early prototype  
**Visibility:** Private during early development  
**Primary goal:** Build a practical, offline-capable workflow system for small businesses.

## Core idea

Local-First Operations Tracker is designed to run on one company-owned computer and serve other devices through the local network using a browser-based interface.

The system should keep working even if the external internet connection is down. It can also be deployed to the cloud later if the business needs remote access, more performance, or centralized hosting.

## First target use case

The first practical use case is a laundry / textile service workflow:

- customer details
- incoming laundry date
- requested pickup date
- next-business-day pickup reminders
- job status tracking
- printable receipts
- sequential receipt numbers
- products and pricing
- inventory value tracking

The system is not intended to be laundry-only. The workflow, statuses, receipt templates, fields, and business settings should be configurable for other industries.

## Planned deployment modes

- **Local only:** one server machine, browser access inside the company network
- **Offline isolated:** no required internet connection
- **VPN / Tailscale:** optional secure remote access
- **Cloud-ready:** later deployment to AWS or another cloud provider

## Quick start on Windows

Clone the repository:

```powershell
git clone https://github.com/denzo69/Local-First-Operations-Tracker.git
cd Local-First-Operations-Tracker
```

Start the development server:

```powershell
.\run.bat
```

Open the app:

```text
http://127.0.0.1:8000
```

Health check:

```text
http://127.0.0.1:8000/health
```

Run tests:

```powershell
.\.venv\Scripts\activate
pytest
```

## Local network access

The default `run.bat` starts the app on `127.0.0.1`, which means it is only available on the same computer.

For local network testing, change the Uvicorn host to `0.0.0.0` and open the Windows firewall only for the trusted local network.

Example:

```powershell
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Then open the app from another device using the server computer's local IP address:

```text
http://192.168.x.x:8000
```

Do not expose the development server directly to the public internet.

## Documentation

Initial design documents live in the `docs/` directory:

- [`docs/Vision.md`](docs/Vision.md)
- [`docs/Software_Design_Document.md`](docs/Software_Design_Document.md)
- [`docs/Architecture.md`](docs/Architecture.md)
- [`docs/Backup_and_Failover.md`](docs/Backup_and_Failover.md)
- [`docs/Database.md`](docs/Database.md)
- [`docs/API.md`](docs/API.md)
- [`docs/Roadmap.md`](docs/Roadmap.md)
- [`docs/UI/Wireframes.md`](docs/UI/Wireframes.md)

## Technology direction

Planned stack for the MVP:

- Python
- FastAPI
- SQLite
- SQLAlchemy
- Jinja2
- HTMX
- Bootstrap
- Pytest

Later versions may add PostgreSQL, Docker, cloud storage, users and roles, and real-time replication.
