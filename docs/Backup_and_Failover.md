# Backup and Failover Plan

## Goal

The system must protect business data if the primary server machine fails, the database becomes corrupted, the network fails, or a user makes a mistake.

Backups are a core feature, not an optional extra.

## Backup principles

1. Backups must be automatic.
2. Backups must be visible to the user.
3. Restore must be documented and testable.
4. Local operation must not depend on cloud services.
5. Backup failures must be shown clearly in the UI.

## MVP backup strategy

MVP should include scheduled local backups.

### Required MVP features

- automatic SQLite backup every 5 to 15 minutes
- backup on application shutdown where possible
- backup before database migration or software update
- manual "Create backup" button
- backup list in the UI
- restore from selected backup
- backup retention policy
- backup health indicator on dashboard or settings page

### Backup directory

Default local backup path:

```text
/backups/
```

Example backup filename:

```text
ops_tracker_2026-07-09_0700.sqlite.zip
```

## Near-real-time backup strategy

Later versions should support copying backups or database snapshots to another location:

- another local computer
- NAS
- external drive
- network share
- encrypted cloud storage

The system should show:

- last successful backup time
- last successful remote copy time
- backup destination
- backup status
- warning if backup is stale

## SQLite considerations

SQLite is good for the local MVP, but live file copying must be handled safely.

Recommended approach:

- use SQLite WAL mode
- use SQLite backup API or safe snapshot method
- compress backups after snapshot creation
- never copy a database file while assuming it is consistent without a safe method

## PostgreSQL future option

For more advanced failover, PostgreSQL is a better long-term option because it supports stronger replication features.

Possible future architecture:

```text
Primary server
    |
    | streaming replication
    v
Secondary server
```

## Failover levels

### Level 1 — Manual restore

If the main server fails, install the app on another machine and restore from the latest backup.

This is enough for MVP.

### Level 2 — Warm standby

A second machine receives frequent backups. If the main machine fails, the user starts the app on the second machine and restores the latest snapshot.

### Level 3 — Hot standby

A second server runs continuously with near-real-time replication. Users can switch to the standby server with minimal downtime.

This is not MVP scope.

## UI requirements

The application should include a backup status panel:

```text
Backup status: OK
Last local backup: 07:00
Last remote copy: 07:02
Backup destination: NAS-01/OpsTracker
Oldest retained backup: 2026-07-01
```

If backups fail:

```text
Backup status: WARNING
Last successful backup: yesterday 16:45
Reason: backup destination unavailable
```

## Restore requirements

Restore flow:

1. select backup
2. show backup metadata
3. create safety copy of current database
4. restore selected backup
5. restart application if needed
6. show confirmation

## Security

Backups may contain customer data. Later versions should support:

- encrypted backup archives
- password-protected exports
- access control for restore actions
- audit log entry when backup or restore is performed

## MVP acceptance criteria

- A backup can be created manually.
- A backup is created automatically on schedule.
- The user can see the last successful backup time.
- The user can restore from a backup.
- Tests cover backup file creation and retention logic.
