# Backups

Default local database:

```text
data/app.sqlite
```

Default backup folder:

```text
backups/
```

Backups are created with SQLite's backup API, validated with `PRAGMA integrity_check`, and listed in the Backups page. Restore creates a safety backup before replacing the current database.

Current backup functionality includes:

- manual backup creation
- restore with maintenance-mode protection
- backup health status
- retention cleanup
- checksum and integrity validation

There is no automatic background backup scheduler yet. For production-like local use, pair manual app backups with an external backup routine until scheduled backups are implemented.
