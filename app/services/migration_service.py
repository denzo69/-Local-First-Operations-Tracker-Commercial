from sqlalchemy import text
from sqlalchemy.engine import Engine


def ensure_sqlite_schema_compatibility(engine: Engine) -> list[str]:
    diagnostics: list[str] = []
    if engine.dialect.name != "sqlite":
        return diagnostics

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
        _add_column_if_missing(connection, "users", "password_hash", "VARCHAR(255)")
        _add_column_if_missing(connection, "users", "can_receive_sales_credit", "BOOLEAN DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "freight_vat_rate", "NUMERIC(5, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "freight_vat_amount", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "freight_total_inc_vat", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "other_costs_vat_rate", "NUMERIC(5, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "other_costs_vat_amount", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "other_costs_total_inc_vat", "NUMERIC(12, 2) DEFAULT 0")
        _create_inventory_transaction_immutability_triggers(connection)
        diagnostics.extend(
            _create_partial_unique_index_if_safe(
                connection,
                table="shifts",
                columns=["seller_id"],
                index_name="ux_open_shift_seller",
                where_clause="status = 'open'",
            )
        )
        diagnostics.extend(
            _create_partial_unique_index_if_safe(
                connection,
                table="shifts",
                columns=["cash_register_id"],
                index_name="ux_open_shift_register",
                where_clause="status = 'open'",
            )
        )
    return diagnostics


def _create_inventory_transaction_immutability_triggers(connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).fetchall()
    }
    if "inventory_transactions" not in tables:
        return
    connection.execute(
        text(
            """
            CREATE TRIGGER IF NOT EXISTS trg_inventory_transactions_no_update
            BEFORE UPDATE ON inventory_transactions
            BEGIN
                SELECT RAISE(ABORT, 'inventory_transactions are immutable');
            END
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TRIGGER IF NOT EXISTS trg_inventory_transactions_no_delete
            BEFORE DELETE ON inventory_transactions
            BEGIN
                SELECT RAISE(ABORT, 'inventory_transactions are immutable');
            END
            """
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
    table_exists = connection.execute(
        text("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = :table"),
        {"table": table},
    ).first()
    if table_exists is None:
        return
    columns = {
        row[1]
        for row in connection.execute(text(f"PRAGMA table_info({table})")).fetchall()
    }
    if column not in columns:
        connection.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))


def _create_partial_unique_index_if_safe(
    connection,
    *,
    table: str,
    columns: list[str],
    index_name: str,
    where_clause: str,
) -> list[str]:
    column_list = ", ".join(columns)
    duplicate = connection.execute(
        text(
            f"""
            SELECT {column_list}, COUNT(*) AS duplicate_count
            FROM {table}
            WHERE {where_clause}
            GROUP BY {column_list}
            HAVING COUNT(*) > 1
            LIMIT 1
            """
        )
    ).first()
    if duplicate is not None:
        return [
            f"Skipped {index_name}: duplicate rows exist for {table}({column_list}) where {where_clause}."
        ]

    connection.execute(
        text(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {index_name} "
            f"ON {table} ({column_list}) WHERE {where_clause}"
        )
    )
    return []
