# Architecture

## Deployment model

The application is designed to support multiple deployment modes while keeping the same core application code.

### Local network mode

```text
Windows server PC
    |
    | FastAPI + SQLite + web UI
    |
Local network
    |---- Office PC browser
    |---- Tablet browser
    |---- Phone browser
```

### Offline isolated mode

The system can be installed on a machine or local network with no external internet access. In this mode, the application must not require external API calls, cloud login, external analytics, or license checks.

### VPN / Tailscale mode

Optional secure remote access can be added by exposing the local server only through a private VPN connection.

### Cloud mode

Cloud deployment is planned for later versions.

```text
Users
  |
HTTPS
  |
Reverse proxy
  |
FastAPI app
  |
PostgreSQL database
  |
Object/file storage
```

## Application layers

```text
Browser UI
  |
Routes / Controllers
  |
Services / Business logic
  |
Repository / Database access
  |
Database
```

## Planned project structure

```text
local-first-ops-tracker/
├── app/
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py
│   ├── schemas.py
│   ├── services/
│   ├── routes/
│   ├── templates/
│   └── static/
├── data/
├── backups/
├── docs/
├── tests/
├── run.bat
├── requirements.txt
└── README.md
```

## Key services

- `receipt_number_service.py` — sequential receipt number generation
- `reminder_service.py` — pickup and business-day reminder rules
- `print_service.py` — printable document rendering
- `backup_service.py` — backups, restore points, backup health checks
- `settings_service.py` — configurable workflows and business settings

## Database strategy

MVP uses SQLite for simple local installation.

Later cloud or multi-user installations may use PostgreSQL.

The application should isolate database-specific behavior behind services where practical so that migration from SQLite to PostgreSQL remains realistic.

## Network assumptions

For local mode, the application runs on one machine and listens on the local network. Other devices access it through a browser.

The application should document how to bind safely to LAN addresses and how to avoid exposing the service directly to the public internet.
