# Local-First Operations Tracker

A configurable local-first operations tracker for small businesses.

## Project status

**Status:** Design phase  
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
