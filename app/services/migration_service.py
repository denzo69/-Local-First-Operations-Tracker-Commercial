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
        _add_column_if_missing(connection, "sales", "sold_by_user_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "created_by_user_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "cash_register_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "created_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "seller_override_reason", "TEXT")
        _add_column_if_missing(connection, "sales", "seller_overridden_by_user_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "seller_overridden_at", "DATETIME")
        _add_column_if_missing(connection, "payments", "received_by_user_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "cost_of_goods_sold_ex_vat", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "sales", "gross_profit_ex_vat", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "sales", "gross_margin_percent", "NUMERIC(7, 3)")
        _add_column_if_missing(connection, "sale_lines", "cost_of_goods_sold_ex_vat", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "sale_lines", "gross_profit_ex_vat", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "sale_lines", "gross_margin_percent", "NUMERIC(7, 3)")
        _add_column_if_missing(connection, "sales", "source_type", "VARCHAR(50)")
        _add_column_if_missing(connection, "sales", "idempotency_key", "VARCHAR(100)")
        _add_column_if_missing(connection, "sales", "finalized_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "settlement_status", "VARCHAR(50)")
        _add_column_if_missing(connection, "sales", "invoice_customer_snapshot_json", "TEXT")
        _add_column_if_missing(connection, "sales", "transferred_to_invoicing_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "external_invoice_service", "VARCHAR(120)")
        _add_column_if_missing(connection, "sales", "external_invoice_number", "VARCHAR(120)")
        _add_column_if_missing(connection, "sales", "invoice_date", "DATE")
        _add_column_if_missing(connection, "sales", "due_date", "DATE")
        _add_column_if_missing(connection, "sales", "external_invoice_reference", "VARCHAR(255)")
        _add_column_if_missing(connection, "sales", "invoice_handoff_notes", "TEXT")
        _add_column_if_missing(connection, "sales", "payment_status_checked_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "paid_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "next_follow_up_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "reminder_count", "INTEGER DEFAULT 0")
        _add_column_if_missing(connection, "sales", "last_reminder_sent_at", "DATETIME")
        _add_column_if_missing(connection, "sales", "follow_up_notes", "TEXT")
        _add_column_if_missing(connection, "sales", "business_date", "DATE")
        _add_column_if_missing(connection, "sales", "customer_id", "INTEGER")
        _add_column_if_missing(connection, "sales", "customer_name_snapshot", "VARCHAR(255)")
        _add_column_if_missing(connection, "goods_receipts", "freight_vat_rate", "NUMERIC(5, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "freight_vat_amount", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "freight_total_inc_vat", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "other_costs_vat_rate", "NUMERIC(5, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "other_costs_vat_amount", "NUMERIC(12, 2) DEFAULT 0")
        _add_column_if_missing(connection, "goods_receipts", "other_costs_total_inc_vat", "NUMERIC(12, 2) DEFAULT 0")
        _backfill_sales_compatibility_columns(connection)
        _create_sales_compatibility_indexes(connection)
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


def _backfill_sales_compatibility_columns(connection) -> None:
    tables = {
        row[0]
        for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).fetchall()
    }
    if "sales" in tables:
        connection.execute(text("UPDATE sales SET sold_by_user_id = seller_id WHERE sold_by_user_id IS NULL"))
        connection.execute(text("UPDATE sales SET created_by_user_id = seller_id WHERE created_by_user_id IS NULL"))
        if "shifts" in tables:
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
        connection.execute(text("UPDATE sales SET finalized_at = sold_at WHERE finalized_at IS NULL"))
        if "shifts" in tables:
            connection.execute(
                text(
                    """
                    UPDATE sales
                    SET business_date = (
                        SELECT shifts.business_date
                        FROM shifts
                        WHERE shifts.id = sales.shift_id
                    )
                    WHERE business_date IS NULL AND shift_id IS NOT NULL
                    """
                )
            )
        connection.execute(
            text(
                """
                UPDATE sales
                SET business_date = date(COALESCE(sold_at, finalized_at, created_at, CURRENT_TIMESTAMP))
                WHERE business_date IS NULL
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE sales
                SET source_type = CASE WHEN work_order_id IS NULL THEN 'pos' ELSE 'work_order' END
                WHERE source_type IS NULL OR source_type = ''
                """
            )
        )
        connection.execute(
            text(
                """
                UPDATE sales
                SET settlement_status = CASE WHEN status = 'refunded' THEN 'refunded' ELSE 'paid' END
                WHERE settlement_status IS NULL OR settlement_status = ''
                """
            )
        )
        connection.execute(text("UPDATE sales SET reminder_count = 0 WHERE reminder_count IS NULL"))

    if "payments" in tables:
        if "sales" in tables:
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

    if "users" in tables:
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


def _create_sales_compatibility_indexes(connection) -> None:
    _create_index_if_missing(connection, "ix_sales_sold_by_user_id", "sales", "sold_by_user_id")
    _create_index_if_missing(connection, "ix_sales_created_by_user_id", "sales", "created_by_user_id")
    _create_index_if_missing(connection, "ix_sales_cash_register_id", "sales", "cash_register_id")
    _create_index_if_missing(connection, "ix_payments_received_by_user_id", "payments", "received_by_user_id")
    _create_index_if_missing(connection, "ix_sales_source_type", "sales", "source_type")
    _create_index_if_missing(connection, "ix_sales_settlement_status", "sales", "settlement_status")
    _create_unique_index_if_safe(connection, table="sales", column="idempotency_key", index_name="ix_sales_idempotency_key")
    _create_partial_unique_index_if_safe(
        connection,
        table="sales",
        columns=["work_order_id"],
        index_name="ux_sales_active_work_order",
        where_clause="work_order_id IS NOT NULL AND status != 'cancelled'",
    )
    _create_index_if_missing(connection, "ix_sales_external_invoice_number", "sales", "external_invoice_number")
    _create_index_if_missing(connection, "ix_sales_due_date", "sales", "due_date")
    _create_index_if_missing(connection, "ix_sales_next_follow_up_at", "sales", "next_follow_up_at")
    _create_index_if_missing(connection, "ix_sales_business_date", "sales", "business_date")
    _create_index_if_missing(connection, "ix_sales_customer_id", "sales", "customer_id")


def _create_index_if_missing(connection, index_name: str, table: str, column: str) -> None:
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
        return
    connection.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {table} ({column})"))


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
