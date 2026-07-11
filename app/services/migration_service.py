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
        connection.execute(text("UPDATE users SET can_receive_sales_credit = 0 WHERE can_receive_sales_credit IS NULL"))
        connection.execute(
            text(
                """
                UPDATE users
                SET can_receive_sales_credit = 1
                WHERE role_id IN (SELECT id FROM roles WHERE code = 'seller')
                """
            )
        )
        _add_column_if_missing(connection, "sales", "sold_by_user_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "created_by_user_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "cash_register_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "created_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "seller_override_reason", "TEXT")
        _add_column_if_missing(connection, "sales", "seller_overridden_by_user_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "seller_overridden_at", "DATETIME")
        _add_column_if_missing(connection, "payments", "received_by_user_id", "INTEGER")
        connection.execute(text("UPDATE sales SET sold_by_user_id = seller_id WHERE sold_by_user_id IS NULL"))
        connection.execute(text("UPDATE sales SET created_by_user_id = seller_id WHERE created_by_user_id IS NULL"))
        connection.execute(
            text(
                """
                UPDATE sales
                SET cash_register_id = (
                    SELECT shifts.cash_register_id
                    FROM shifts
                    WHERE shifts.id = sales.shift_id
                )
                WHERE cash_register_id IS NULL
                """
            )
        )
        connection.execute(text("UPDATE sales SET created_at = sold_at WHERE created_at IS NULL"))
        connection.execute(
            text(
                """
                UPDATE payments
                SET received_by_user_id = (
                    SELECT sales.created_by_user_id
                    FROM sales
                    WHERE sales.id = payments.sale_id
                )
                WHERE received_by_user_id IS NULL
                """
            )
        )
        connection.execute(text("UPDATE payments SET received_by_user_id = seller_id WHERE received_by_user_id IS NULL"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_sales_sold_by_user_id ON sales (sold_by_user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_sales_created_by_user_id ON sales (created_by_user_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_sales_cash_register_id ON sales (cash_register_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_payments_received_by_user_id ON payments (received_by_user_id)"))
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
