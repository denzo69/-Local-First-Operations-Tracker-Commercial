# Software Design Document

## 1. Overview

Local-First Operations Tracker is a browser-based operations tracking system for small businesses. It runs on a primary company machine and is accessed by other devices through the local network.

The application should support a local installation first and a cloud-ready architecture later.

## 2. Core modules

### Customers

Stores customer identity and contact details.

Planned fields:

- name
- phone
- email
- address
- company name
- business ID
- notes

### Jobs / Orders

Stores incoming work and tracks it through a configurable workflow.

Planned fields:

- job number
- receipt number
- customer
- title
- description
- arrival date
- requested pickup date
- status
- priority
- notes

### Status workflow

Statuses must be configurable in settings.

Laundry preset example:

1. Received
2. In progress
3. Washing
4. Drying
5. Packed
6. Ready for pickup
7. Picked up

### Dashboard

The dashboard is the daily command center. It must show:

- overdue jobs
- jobs due today
- jobs due tomorrow
- jobs that need attention before the next business day
- ready but uncollected jobs

### Products and pricing

Products or service items can be added to jobs.

Planned fields:

- name
- description
- unit price
- VAT percent
- unit
- active / hidden
- stock item flag

### Receipts and print documents

The system must support printable and editable receipt previews.

Document types:

- incoming receipt
- pickup receipt
- work order
- customer receipt

### Inventory

Inventory starts as a later phase feature.

Planned fields:

- product
- stock balance
- purchase price
- sale price
- reorder level
- inventory value

### Settings

Settings must make the application adaptable to multiple industries.

Configurable items:

- company information
- industry profile
- workflow statuses
- custom fields
- receipt templates
- receipt numbering
- VAT defaults
- backup settings
- deployment mode

## 3. MVP scope

The first build should include:

1. customer CRUD
2. job CRUD
3. configurable statuses
4. pickup dates
5. dashboard reminders
6. sequential receipt numbers
7. printable receipt preview
8. products and pricing
9. job item rows
10. total calculation

## 4. Technical direction

MVP stack:

- FastAPI
- SQLite
- SQLAlchemy
- Jinja2
- HTMX
- Bootstrap
- Pytest

## 5. Quality requirements

The system should be:

- easy to install on Windows
- usable from phones and tablets
- reliable without internet
- backed up automatically
- simple to restore after failure
- documented well enough for portfolio review

## 6. Future direction

Later versions may add:

- PostgreSQL
- Docker
- cloud deployment
- users and roles
- audit log
- inventory events
- PDF export
- Excel / CSV export
- barcode or QR code support
- AI-assisted document and workflow features
