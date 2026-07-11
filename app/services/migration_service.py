from sqlalchemy import text
from sqlalchemy.engine import Engine


def ensure_sqlite_schema_compatibility(engine: Engine) -> None:
    if engine.dialect.name != "sqlite":
        return

    with engine.begin() as connection:
        _create_unique_index_if_safe(
            connection,
            table="jobs",
            column="receipt_number",
            index_name="ux_jobs_receipt_number",
        )
        _create_unique_index_if_safe(
            connection,
            table="receipts",
            column="receipt_number",
            index_name="ux_receipts_receipt_number",
        )
        _add_column_if_missing(connection, "refunds", "vat_breakdown_json", "TEXT")
        _add_column_if_missing(connection, "daily_closings", "reopen_reason", "TEXT")
        _add_column_if_missing(
            connection,
            "daily_closings",
            "current_version",
            "INTEGER DEFAULT 0",
        )
        _add_column_if_missing(
            connection,
            "daily_closing_snapshots",
            "version",
            "INTEGER DEFAULT 1 NOT NULL",
        )
        _add_column_if_missing(
            connection,
            "daily_closing_snapshots",
            "schema_version",
            "INTEGER DEFAULT 1 NOT NULL",
        )
        _add_column_if_missing(
            connection,
            "daily_closing_snapshots",
            "created_by_user_id",
            "INTEGER",
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_open_shift_seller "
                "ON shifts (seller_id) WHERE status = 'open'"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_open_shift_register "
                "ON shifts (cash_register_id) WHERE status = 'open'"
            )
        )


def _create_unique_index_if_safe(connection, *, table: str, column: str, index_name: str) -> None:
    duplicates = connection.execute(
        text(
            f"""
            SELECT {column}
            FROM {table}
            WHERE {column} IS NOT NULL AND {column} != ''
            GROUP BY {column}
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicates is not None:
        return

    connection.execute(
        text(f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} ON {table} ({column})")
    )


def _add_column_if_missing(connection, table: str, column: str, definition: str) -> None:
    columns = {
        row[1]
        for row in connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
    }
    if column not in columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
