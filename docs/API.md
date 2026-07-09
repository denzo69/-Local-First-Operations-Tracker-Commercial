# API Outline

The MVP can be built as server-rendered HTML using FastAPI routes, Jinja2, HTMX, and Bootstrap.

A JSON API can be added gradually where useful.

## Customers

Planned routes:

```text
GET    /customers
GET    /customers/new
POST   /customers
GET    /customers/{id}
GET    /customers/{id}/edit
POST   /customers/{id}
POST   /customers/{id}/delete
```

## Jobs

Planned routes:

```text
GET    /jobs
GET    /jobs/new
POST   /jobs
GET    /jobs/{id}
GET    /jobs/{id}/edit
POST   /jobs/{id}
POST   /jobs/{id}/status
POST   /jobs/{id}/delete
```

## Dashboard

Planned routes:

```text
GET /dashboard
GET /
```

Dashboard data:

- overdue jobs
- today pickups
- tomorrow pickups
- next-business-day warnings
- ready but uncollected jobs

## Products

Planned routes:

```text
GET    /products
GET    /products/new
POST   /products
GET    /products/{id}/edit
POST   /products/{id}
POST   /products/{id}/delete
```

## Job items

Planned routes:

```text
POST /jobs/{id}/items
POST /jobs/{id}/items/{item_id}/delete
```

## Receipts and printing

Planned routes:

```text
GET  /jobs/{id}/receipt
POST /jobs/{id}/receipt/preview
POST /jobs/{id}/receipt/print-log
```

## Settings

Planned routes:

```text
GET  /settings
POST /settings/company
POST /settings/statuses
POST /settings/receipt-numbering
POST /settings/backup
```

## Backups

Planned routes:

```text
GET  /backups
POST /backups/create
POST /backups/{backup_id}/restore
```

## Future JSON API

Later versions can expose API endpoints under:

```text
/api/v1/
```

This can support mobile apps, external integrations, or a richer frontend later.
