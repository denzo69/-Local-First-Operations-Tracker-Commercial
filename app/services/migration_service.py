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
