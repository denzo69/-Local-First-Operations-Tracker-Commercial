"""Safe Alembic bootstrap for legacy unstamped SQLite databases.

Older local-first builds created tables directly through application startup.
Those databases can contain a valid current schema without an Alembic stamp.
This module classifies the schema before stamping anything so Alembic never
tries to recreate existing tables blindly.
"""

from __future__ import annotations

import argparse
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlparse

from alembic import command
from alembic.config import Config

from app.config import get_settings


BASELINE_REVISION = "162323fcac91"
AUTH_REVISION = "3f0d1c9a8b22"
INVENTORY_REVISION = "7c2a91f4d8e3"
STABILIZATION_REVISION = "9e4c3b2a1f08"
UNIFIED_SALES_REVISION = "a4d7b9c2e1f3"
INVOICE_FOLLOWUP_REVISION = "b5c8d2e4f6a1"
SALE_DOCUMENT_REVISION = "c9d1e7a4b2f6"
OPTIONAL_SHIFTS_REVISION = "e2f4a6b8c0d1"
SHIFTLESS_REFUNDS_REVISION = "f3a9b7c1d2e4"
QUICK_SALE_CUSTOMER_REVISION = "a8c1e3f5b7d9"
DOCUMENT_WORKFLOW_REVISION = "b9d2e4f6a8c0"
CUSTOMER_DISCOUNT_REVISION = "d6e8f0a1b2c3"
HEAD_REVISION = CUSTOMER_DISCOUNT_REVISION

CLASS_EMPTY = "empty database"
CLASS_BASELINE = "matches baseline"
CLASS_AUTH = "matches auth revision"
CLASS_INVENTORY = "matches inventory revision"
CLASS_STABILIZATION = "matches stabilization revision"
CLASS_UNIFIED_SALES = "matches unified sales revision"
CLASS_INVOICE_FOLLOWUP = "matches invoice follow-up revision"
CLASS_SALE_DOCUMENTS = "matches sale document numbering revision"
CLASS_OPTIONAL_SHIFTS = "matches optional cashier shifts revision"
CLASS_SHIFTLESS_REFUNDS = "matches shiftless refunds revision"
CLASS_QUICK_SALE_CUSTOMER = "matches quick sale customer revision"
CLASS_DOCUMENT_WORKFLOW = "matches document workflow revision"
CLASS_CUSTOMER_DISCOUNT = "matches customer default discount revision"
CLASS_UNKNOWN = "inconsistent / partially migrated / unknown"


BASELINE_TABLE_COLUMNS: dict[str, set[str]] = {
    "audit_log": {"id", "event_type", "entity_type", "entity_id", "description", "created_at"},
    "cash_registers": {"id", "name", "location", "is_active", "created_at", "updated_at"},
    "customers": {
        "id",
        "name",
        "phone",
        "email",
        "address",
        "company_name",
        "business_id",
        "notes",
        "created_at",
        "updated_at",
    },
    "job_statuses": {
        "id",
        "name",
        "sort_order",
        "is_final",
        "is_ready_state",
        "is_packed_state",
        "is_active",
    },
    "products": {
        "id",
        "name",
        "description",
        "unit_price",
        "vat_percent",
        "unit",
        "is_active",
        "is_stock_item",
        "created_at",
        "updated_at",
    },
    "roles": {"id", "code", "name", "created_at"},
    "settings": {"id", "key", "value", "updated_at"},
    "jobs": {
        "id",
        "job_number",
        "receipt_number",
        "customer_id",
        "title",
        "description",
        "arrival_date",
        "requested_pickup_date",
        "status_id",
        "priority",
        "notes",
        "created_at",
        "updated_at",
    },
    "users": {"id", "name", "login_name", "is_active", "role_id", "created_at", "updated_at"},
    "daily_closings": {
        "id",
        "business_date",
        "created_by_user_id",
        "closed_at",
        "reopened_at",
        "reopened_by_user_id",
        "reopen_reason",
        "status",
        "current_version",
        "total_sales",
        "total_refunds",
        "total_discounts",
        "expected_cash",
        "counted_cash",
        "cash_over_short",
    },
    "job_items": {
        "id",
        "job_id",
        "product_id",
        "description",
        "quantity",
        "unit_price",
        "vat_percent",
        "line_total",
        "created_at",
        "updated_at",
    },
    "receipts": {
        "id",
        "job_id",
        "receipt_number",
        "receipt_type",
        "printed_at",
        "editable_snapshot",
        "created_at",
    },
    "shifts": {
        "id",
        "cash_register_id",
        "seller_id",
        "opened_at",
        "closed_at",
        "business_date",
        "starting_cash",
        "counted_closing_cash",
        "expected_closing_cash",
        "cash_over_short",
        "status",
        "notes",
    },
    "cash_movements": {"id", "shift_id", "seller_id", "movement_type", "amount", "reason", "created_at"},
    "daily_closing_snapshots": {
        "id",
        "daily_closing_id",
        "version",
        "schema_version",
        "created_by_user_id",
        "snapshot_json",
        "created_at",
    },
    "sales": {
        "id",
        "seller_id",
        "shift_id",
        "work_order_id",
        "document_number",
        "sold_at",
        "payment_method",
        "subtotal",
        "vat_total",
        "discount_total",
        "total",
        "vat_breakdown_json",
        "status",
    },
    "payments": {
        "id",
        "sale_id",
        "shift_id",
        "seller_id",
        "payment_method",
        "amount",
        "paid_at",
        "reference",
    },
    "refunds": {
        "id",
        "sale_id",
        "shift_id",
        "seller_id",
        "amount",
        "vat_amount",
        "vat_breakdown_json",
        "reason",
        "refunded_at",
        "payment_method",
    },
    "sale_lines": {
        "id",
        "sale_id",
        "work_order_item_id",
        "product_id",
        "description_snapshot",
        "quantity",
        "unit_price",
        "vat_percent",
        "discount_amount",
        "line_total",
        "vat_amount",
        "created_at",
    },
}

AUTH_COLUMNS = {"users": {"password_hash"}}

INVENTORY_TABLE_COLUMNS: dict[str, set[str]] = {
    "suppliers": {
        "id",
        "name",
        "business_id",
        "contact_name",
        "email",
        "phone",
        "address",
        "notes",
        "is_active",
        "created_at",
        "updated_at",
    },
    "warehouses": {
        "id",
        "name",
        "code",
        "description",
        "address",
        "is_external",
        "is_active",
        "created_at",
        "updated_at",
    },
    "warehouse_locations": {
        "id",
        "warehouse_id",
        "parent_id",
        "code",
        "name",
        "location_type",
        "is_active",
        "sort_order",
        "created_at",
        "updated_at",
    },
    "inventory_balances": {
        "id",
        "product_id",
        "warehouse_location_id",
        "quantity_on_hand",
        "quantity_reserved",
        "quantity_available",
        "weighted_average_cost_ex_vat",
        "inventory_value_ex_vat",
        "updated_at",
    },
    "goods_receipts": {
        "id",
        "supplier_id",
        "receipt_date",
        "delivery_number",
        "invoice_number",
        "freight_total_ex_vat",
        "other_costs_total_ex_vat",
        "allocation_method",
        "status",
        "received_by_user_id",
        "posted_at",
        "cancelled_at",
        "cancellation_reason",
        "notes",
        "created_at",
        "updated_at",
    },
    "goods_receipt_lines": {
        "id",
        "goods_receipt_id",
        "product_id",
        "destination_location_id",
        "quantity",
        "purchase_unit_price_ex_vat",
        "vat_rate",
        "purchase_unit_price_inc_vat",
        "allocated_freight_ex_vat",
        "allocated_other_costs_ex_vat",
        "landed_unit_cost_ex_vat",
        "line_total_ex_vat",
        "created_at",
    },
    "inventory_transactions": {
        "id",
        "product_id",
        "warehouse_id",
        "shelf_location_id",
        "transaction_type",
        "quantity_change",
        "unit_cost_ex_vat",
        "allocated_freight_cost",
        "allocated_other_cost",
        "total_inventory_cost",
        "inventory_value_before",
        "inventory_value_after",
        "stock_before",
        "stock_after",
        "weighted_average_cost_before",
        "weighted_average_cost_after",
        "supplier_id",
        "purchase_invoice_number",
        "delivery_note_number",
        "goods_receipt_id",
        "work_order_id",
        "sale_id",
        "adjustment_reason",
        "reference",
        "created_by_user_id",
        "created_at",
        "reversal_of_transaction_id",
    },
}

INVENTORY_COLUMNS = {
    "products": {
        "current_weighted_average_cost_ex_vat",
        "current_inventory_quantity",
        "current_inventory_value_ex_vat",
        "current_purchase_price_ex_vat",
        "current_purchase_price_inc_vat",
    },
    "sales": {"cost_of_goods_sold_ex_vat", "gross_profit_ex_vat", "gross_margin_percent"},
    "sale_lines": {"cost_of_goods_sold_ex_vat", "gross_profit_ex_vat", "gross_margin_percent"},
}

STABILIZATION_COLUMNS = {
    "users": {"can_receive_sales_credit"},
    "sales": {
        "sold_by_user_id",
        "created_by_user_id",
        "cash_register_id",
        "created_at",
        "seller_override_reason",
        "seller_overridden_by_user_id",
        "seller_overridden_at",
    },
    "payments": {"received_by_user_id"},
    "goods_receipts": {
        "freight_vat_rate",
        "freight_vat_amount",
        "freight_total_inc_vat",
        "other_costs_vat_rate",
        "other_costs_vat_amount",
        "other_costs_total_inc_vat",
    },
}

UNIFIED_SALES_COLUMNS = {
    "sales": {
        "source_type",
        "idempotency_key",
        "finalized_at",
        "settlement_status",
        "invoice_customer_snapshot_json",
    },
}

INVOICE_FOLLOWUP_COLUMNS = {
    "sales": {
        "transferred_to_invoicing_at",
        "external_invoice_service",
        "external_invoice_number",
        "invoice_date",
        "due_date",
        "external_invoice_reference",
        "invoice_handoff_notes",
        "payment_status_checked_at",
        "paid_at",
        "next_follow_up_at",
        "reminder_count",
        "last_reminder_sent_at",
        "follow_up_notes",
    },
}

SALE_DOCUMENT_SETTING_KEYS = {
    "sale_document_prefix",
    "sale_document_padding",
    "sale_document_annual_reset",
    "next_sale_document_sequence",
    "sale_document_sequence_year",
}

OPTIONAL_SHIFTS_COLUMNS = {
    "sales": {"business_date"},
}

SHIFTLESS_REFUNDS_COLUMNS = {
    "refunds": {"business_date"},
}

QUICK_SALE_CUSTOMER_COLUMNS = {
    "sales": {"customer_id", "customer_name_snapshot"},
}

DOCUMENT_WORKFLOW_COLUMNS = {
    "jobs": {"document_type", "source_job_id", "converted_at"},
}

CUSTOMER_DISCOUNT_COLUMNS = {
    "customers": {"default_discount_percent"},
}

OPTIONAL_SHIFTS_SETTING_KEYS = {"require_cashier_shift"}

NULLABLE_COLUMNS_BY_REVISION = {
    OPTIONAL_SHIFTS_REVISION: {
        "sales": {"seller_id", "shift_id", "business_date"},
        "payments": {"seller_id", "shift_id"},
    },
    SHIFTLESS_REFUNDS_REVISION: {
        "refunds": {"shift_id", "business_date"},
    },
    QUICK_SALE_CUSTOMER_REVISION: {},
    DOCUMENT_WORKFLOW_REVISION: {},
    CUSTOMER_DISCOUNT_REVISION: {},
}

REQUIRED_INDEXES_BY_REVISION = {
    BASELINE_REVISION: {
        "ix_audit_log_id",
        "ix_cash_registers_id",
        "ix_cash_registers_name",
        "ix_customers_id",
        "ix_customers_name",
        "ix_job_statuses_id",
        "ix_products_id",
        "ix_products_name",
        "ix_roles_code",
        "ix_roles_id",
        "ix_settings_id",
        "ix_settings_key",
        "ix_jobs_id",
        "ix_jobs_job_number",
        "ix_jobs_receipt_number",
        "ix_jobs_requested_pickup_date",
        "ix_users_id",
        "ix_users_login_name",
        "ix_users_name",
        "ix_daily_closings_business_date",
        "ix_daily_closings_id",
        "ix_daily_closings_status",
        "ix_job_items_id",
        "ix_receipts_id",
        "ix_receipts_receipt_number",
        "ix_shifts_business_date",
        "ix_shifts_id",
        "ix_shifts_status",
        "ux_open_shift_seller",
        "ux_open_shift_register",
        "ix_cash_movements_id",
        "ix_daily_closing_snapshots_id",
        "ix_sales_document_number",
        "ix_sales_id",
        "ix_sales_sold_at",
        "ix_sales_status",
        "ix_payments_id",
        "ix_payments_payment_method",
        "ix_refunds_id",
        "ix_sale_lines_id",
    },
    INVENTORY_REVISION: {
        "ix_suppliers_id",
        "ix_suppliers_name",
        "ix_warehouses_id",
        "ix_warehouses_code",
        "ix_warehouses_name",
        "ix_warehouse_locations_id",
        "ix_warehouse_locations_code",
        "ix_inventory_balances_id",
        "ix_goods_receipts_id",
        "ix_goods_receipts_receipt_date",
        "ix_goods_receipts_delivery_number",
        "ix_goods_receipts_invoice_number",
        "ix_goods_receipts_status",
        "ix_goods_receipt_lines_id",
        "ix_inventory_transactions_id",
        "ix_inventory_transactions_warehouse_id",
        "ix_inventory_transactions_shelf_location_id",
        "ix_inventory_transactions_transaction_type",
        "ix_inventory_transactions_supplier_id",
        "ix_inventory_transactions_purchase_invoice_number",
        "ix_inventory_transactions_delivery_note_number",
        "ix_inventory_transactions_created_at",
    },
    STABILIZATION_REVISION: {
        "ix_sales_sold_by_user_id",
        "ix_sales_created_by_user_id",
        "ix_sales_cash_register_id",
        "ix_payments_received_by_user_id",
    },
    UNIFIED_SALES_REVISION: {
        "ix_sales_source_type",
        "ix_sales_settlement_status",
        "ix_sales_idempotency_key",
        "ux_sales_active_work_order",
    },
    INVOICE_FOLLOWUP_REVISION: {
        "ix_sales_external_invoice_number",
        "ix_sales_due_date",
        "ix_sales_next_follow_up_at",
    },
    OPTIONAL_SHIFTS_REVISION: {
        "ix_sales_business_date",
    },
    SHIFTLESS_REFUNDS_REVISION: {
        "ix_refunds_business_date",
    },
    QUICK_SALE_CUSTOMER_REVISION: {
        "ix_sales_customer_id",
    },
    DOCUMENT_WORKFLOW_REVISION: {
        "ix_jobs_document_type",
        "ix_jobs_source_job_id",
    },
    CUSTOMER_DISCOUNT_REVISION: set(),
}

REQUIRED_TRIGGERS_BY_REVISION = {
    STABILIZATION_REVISION: {
        "trg_inventory_transactions_no_update",
        "trg_inventory_transactions_no_delete",
    }
}

REVISION_LABELS = {
    BASELINE_REVISION: CLASS_BASELINE,
    AUTH_REVISION: CLASS_AUTH,
    INVENTORY_REVISION: CLASS_INVENTORY,
    STABILIZATION_REVISION: CLASS_STABILIZATION,
    UNIFIED_SALES_REVISION: CLASS_UNIFIED_SALES,
    INVOICE_FOLLOWUP_REVISION: CLASS_INVOICE_FOLLOWUP,
    SALE_DOCUMENT_REVISION: CLASS_SALE_DOCUMENTS,
    OPTIONAL_SHIFTS_REVISION: CLASS_OPTIONAL_SHIFTS,
    SHIFTLESS_REFUNDS_REVISION: CLASS_SHIFTLESS_REFUNDS,
    QUICK_SALE_CUSTOMER_REVISION: CLASS_QUICK_SALE_CUSTOMER,
    DOCUMENT_WORKFLOW_REVISION: CLASS_DOCUMENT_WORKFLOW,
    CUSTOMER_DISCOUNT_REVISION: CLASS_CUSTOMER_DISCOUNT,
}

REVISION_ORDER = [
    BASELINE_REVISION,
    AUTH_REVISION,
    INVENTORY_REVISION,
    STABILIZATION_REVISION,
    UNIFIED_SALES_REVISION,
    INVOICE_FOLLOWUP_REVISION,
    SALE_DOCUMENT_REVISION,
    OPTIONAL_SHIFTS_REVISION,
    SHIFTLESS_REFUNDS_REVISION,
    QUICK_SALE_CUSTOMER_REVISION,
    DOCUMENT_WORKFLOW_REVISION,
    CUSTOMER_DISCOUNT_REVISION,
]


class MigrationBootstrapError(RuntimeError):
    """Raised when a database cannot be safely migrated."""


@dataclass(frozen=True)
class SchemaInspection:
    database_url: str
    database_path: Path | None
    sqlite: bool
    tables: set[str]
    columns_by_table: dict[str, set[str]]
    nullable_columns_by_table: dict[str, set[str]]
    indexes: set[str]
    foreign_keys: set[tuple[str, str, str]]
    triggers: set[str]
    alembic_versions: tuple[str, ...]
    settings_keys: set[str]
    missing_finalized_sale_document_numbers: int


@dataclass(frozen=True)
class SchemaClassification:
    classification: str
    matched_revision: str | None
    reason: str
    missing: tuple[str, ...] = ()
    unexpected: tuple[str, ...] = ()


@dataclass(frozen=True)
class BootstrapPlan:
    inspection: SchemaInspection
    classification: SchemaClassification
    dry_run: bool
    backup_path: Path | None
    stamp_revision: str | None
    upgrade_target: str | None
    actions: tuple[str, ...]


def sqlite_path_from_url(database_url: str) -> Path | None:
    if database_url == "sqlite:///:memory:":
        return None
    parsed = urlparse(database_url)
    if parsed.scheme != "sqlite":
        return None
    if parsed.netloc:
        return Path(unquote(f"//{parsed.netloc}{parsed.path}"))
    raw_path = unquote(parsed.path)
    if raw_path.startswith("/") and len(raw_path) >= 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    elif raw_path.startswith("/"):
        raw_path = raw_path[1:]
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _connect_sqlite(database_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(database_path))


def inspect_database(database_url: str) -> SchemaInspection:
    database_path = sqlite_path_from_url(database_url)
    if database_path is None:
        return SchemaInspection(
            database_url=database_url,
            database_path=None,
            sqlite=False,
            tables=set(),
            columns_by_table={},
            nullable_columns_by_table={},
            indexes=set(),
            foreign_keys=set(),
            triggers=set(),
            alembic_versions=(),
            settings_keys=set(),
            missing_finalized_sale_document_numbers=0,
        )

    if not database_path.exists():
        return SchemaInspection(
            database_url=database_url,
            database_path=database_path,
            sqlite=True,
            tables=set(),
            columns_by_table={},
            nullable_columns_by_table={},
            indexes=set(),
            foreign_keys=set(),
            triggers=set(),
            alembic_versions=(),
            settings_keys=set(),
            missing_finalized_sale_document_numbers=0,
        )

    with _connect_sqlite(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {row[0] for row in rows}

        columns_by_table: dict[str, set[str]] = {}
        nullable_columns_by_table: dict[str, set[str]] = {}
        foreign_keys: set[tuple[str, str, str]] = set()
        indexes: set[str] = set()
        for table in tables:
            table_info = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
            columns_by_table[table] = {row[1] for row in table_info}
            nullable_columns_by_table[table] = {row[1] for row in table_info if row[3] == 0}
            for row in connection.execute(f'PRAGMA foreign_key_list("{table}")'):
                foreign_keys.add((table, row[3], row[2]))
            for row in connection.execute(f'PRAGMA index_list("{table}")'):
                indexes.add(row[1])

        triggers = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'").fetchall()
        }
        if "alembic_version" in tables:
            alembic_versions = tuple(
                row[0] for row in connection.execute("SELECT version_num FROM alembic_version").fetchall()
            )
        else:
            alembic_versions = ()
        if "settings" in tables and {"key", "value"}.issubset(columns_by_table.get("settings", set())):
            settings_keys = {row[0] for row in connection.execute("SELECT key FROM settings").fetchall()}
        else:
            settings_keys = set()
        missing_finalized_sale_document_numbers = 0
        sale_columns = columns_by_table.get("sales", set())
        if {"document_number", "status"}.issubset(sale_columns):
            finalized_filter = "1 = 1"
            if "finalized_at" in sale_columns:
                finalized_filter = "finalized_at IS NOT NULL"
            missing_finalized_sale_document_numbers = int(
                connection.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM sales
                    WHERE {finalized_filter}
                      AND COALESCE(status, '') != 'cancelled'
                      AND (document_number IS NULL OR document_number = '')
                    """
                ).fetchone()[0]
            )

    return SchemaInspection(
        database_url=database_url,
        database_path=database_path,
        sqlite=True,
        tables=tables,
        columns_by_table=columns_by_table,
        nullable_columns_by_table=nullable_columns_by_table,
        indexes=indexes,
        foreign_keys=foreign_keys,
        triggers=triggers,
        alembic_versions=alembic_versions,
        settings_keys=settings_keys,
        missing_finalized_sale_document_numbers=missing_finalized_sale_document_numbers,
    )


def merge_columns(*definitions: dict[str, set[str]]) -> dict[str, set[str]]:
    merged: dict[str, set[str]] = {}
    for definition in definitions:
        for table, columns in definition.items():
            merged.setdefault(table, set()).update(columns)
    return merged


BASELINE_SCHEMA = merge_columns(BASELINE_TABLE_COLUMNS)
AUTH_SCHEMA = merge_columns(BASELINE_SCHEMA, AUTH_COLUMNS)
INVENTORY_SCHEMA = merge_columns(AUTH_SCHEMA, INVENTORY_COLUMNS, INVENTORY_TABLE_COLUMNS)
STABILIZATION_SCHEMA = merge_columns(INVENTORY_SCHEMA, STABILIZATION_COLUMNS)
UNIFIED_SALES_SCHEMA = merge_columns(STABILIZATION_SCHEMA, UNIFIED_SALES_COLUMNS)
INVOICE_FOLLOWUP_SCHEMA = merge_columns(UNIFIED_SALES_SCHEMA, INVOICE_FOLLOWUP_COLUMNS)
SALE_DOCUMENT_SCHEMA = INVOICE_FOLLOWUP_SCHEMA
OPTIONAL_SHIFTS_SCHEMA = merge_columns(SALE_DOCUMENT_SCHEMA, OPTIONAL_SHIFTS_COLUMNS)
SHIFTLESS_REFUNDS_SCHEMA = merge_columns(OPTIONAL_SHIFTS_SCHEMA, SHIFTLESS_REFUNDS_COLUMNS)
QUICK_SALE_CUSTOMER_SCHEMA = merge_columns(SHIFTLESS_REFUNDS_SCHEMA, QUICK_SALE_CUSTOMER_COLUMNS)
DOCUMENT_WORKFLOW_SCHEMA = merge_columns(QUICK_SALE_CUSTOMER_SCHEMA, DOCUMENT_WORKFLOW_COLUMNS)
CUSTOMER_DISCOUNT_SCHEMA = merge_columns(DOCUMENT_WORKFLOW_SCHEMA, CUSTOMER_DISCOUNT_COLUMNS)
HEAD_KNOWN_SCHEMA = CUSTOMER_DISCOUNT_SCHEMA


def _missing_schema(schema: dict[str, set[str]], inspection: SchemaInspection) -> list[str]:
    missing: list[str] = []
    for table, columns in sorted(schema.items()):
        if table not in inspection.tables:
            missing.append(f"missing table {table}")
            continue
        actual_columns = inspection.columns_by_table.get(table, set())
        for column in sorted(columns - actual_columns):
            missing.append(f"missing column {table}.{column}")
    return missing


def _unexpected_schema(inspection: SchemaInspection) -> list[str]:
    unexpected: list[str] = []
    for table, actual_columns in sorted(inspection.columns_by_table.items()):
        if table not in HEAD_KNOWN_SCHEMA:
            continue
        for column in sorted(actual_columns - HEAD_KNOWN_SCHEMA[table]):
            unexpected.append(f"unexpected column {table}.{column}")
    return unexpected


def _missing_indexes(revision: str, inspection: SchemaInspection) -> list[str]:
    required: set[str] = set()
    revision_index = REVISION_ORDER.index(revision)
    if revision_index >= REVISION_ORDER.index(BASELINE_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[BASELINE_REVISION])
    if revision_index >= REVISION_ORDER.index(INVENTORY_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[INVENTORY_REVISION])
    if revision_index >= REVISION_ORDER.index(STABILIZATION_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[STABILIZATION_REVISION])
    if revision_index >= REVISION_ORDER.index(UNIFIED_SALES_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[UNIFIED_SALES_REVISION])
    if revision_index >= REVISION_ORDER.index(INVOICE_FOLLOWUP_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[INVOICE_FOLLOWUP_REVISION])
    if revision_index >= REVISION_ORDER.index(OPTIONAL_SHIFTS_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[OPTIONAL_SHIFTS_REVISION])
    if revision_index >= REVISION_ORDER.index(SHIFTLESS_REFUNDS_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[SHIFTLESS_REFUNDS_REVISION])
    if revision_index >= REVISION_ORDER.index(QUICK_SALE_CUSTOMER_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[QUICK_SALE_CUSTOMER_REVISION])
    if revision_index >= REVISION_ORDER.index(DOCUMENT_WORKFLOW_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[DOCUMENT_WORKFLOW_REVISION])
    if revision_index >= REVISION_ORDER.index(CUSTOMER_DISCOUNT_REVISION):
        required.update(REQUIRED_INDEXES_BY_REVISION[CUSTOMER_DISCOUNT_REVISION])
    return [f"missing index {index}" for index in sorted(required - inspection.indexes)]


def _missing_nullable_requirements(revision: str, inspection: SchemaInspection) -> list[str]:
    required: dict[str, set[str]] = {}
    revision_index = REVISION_ORDER.index(revision)
    for required_revision, table_columns in NULLABLE_COLUMNS_BY_REVISION.items():
        if revision_index < REVISION_ORDER.index(required_revision):
            continue
        for table, columns in table_columns.items():
            required.setdefault(table, set()).update(columns)

    missing: list[str] = []
    for table, columns in required.items():
        nullable_columns = inspection.nullable_columns_by_table.get(table, set())
        for column in sorted(columns - nullable_columns):
            missing.append(f"column {table}.{column} is not nullable")
    return missing


def _missing_triggers(revision: str, inspection: SchemaInspection) -> list[str]:
    required: set[str] = set()
    if REVISION_ORDER.index(revision) >= REVISION_ORDER.index(STABILIZATION_REVISION):
        required.update(REQUIRED_TRIGGERS_BY_REVISION[STABILIZATION_REVISION])
    return [f"missing trigger {trigger}" for trigger in sorted(required - inspection.triggers)]


def _missing_data_requirements(revision: str, inspection: SchemaInspection) -> list[str]:
    missing: list[str] = []
    if REVISION_ORDER.index(revision) >= REVISION_ORDER.index(SALE_DOCUMENT_REVISION):
        missing_settings = SALE_DOCUMENT_SETTING_KEYS - inspection.settings_keys
        missing.extend(f"missing setting {key}" for key in sorted(missing_settings))
        if inspection.missing_finalized_sale_document_numbers:
            missing.append(
                f"{inspection.missing_finalized_sale_document_numbers} finalized sale(s) missing document_number"
            )
    if REVISION_ORDER.index(revision) >= REVISION_ORDER.index(OPTIONAL_SHIFTS_REVISION):
        missing_settings = OPTIONAL_SHIFTS_SETTING_KEYS - inspection.settings_keys
        missing.extend(f"missing setting {key}" for key in sorted(missing_settings))
    return missing


def _future_revision_evidence(revision: str, inspection: SchemaInspection) -> list[str]:
    revision_index = REVISION_ORDER.index(revision)
    later_revisions = set(REVISION_ORDER[revision_index + 1 :])
    evidence: list[str] = []

    if AUTH_REVISION in later_revisions:
        for table, columns in AUTH_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))

    if INVENTORY_REVISION in later_revisions:
        for table in sorted(set(INVENTORY_TABLE_COLUMNS) & inspection.tables):
            evidence.append(f"future table {table}")
        for table, columns in INVENTORY_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[INVENTORY_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))

    if STABILIZATION_REVISION in later_revisions:
        for table, columns in STABILIZATION_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[STABILIZATION_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))
        present_triggers = REQUIRED_TRIGGERS_BY_REVISION[STABILIZATION_REVISION] & inspection.triggers
        evidence.extend(f"future trigger {trigger}" for trigger in sorted(present_triggers))

    if UNIFIED_SALES_REVISION in later_revisions:
        for table, columns in UNIFIED_SALES_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[UNIFIED_SALES_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))

    if INVOICE_FOLLOWUP_REVISION in later_revisions:
        for table, columns in INVOICE_FOLLOWUP_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[INVOICE_FOLLOWUP_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))

    if SALE_DOCUMENT_REVISION in later_revisions:
        present_settings = SALE_DOCUMENT_SETTING_KEYS & inspection.settings_keys
        evidence.extend(f"future setting {key}" for key in sorted(present_settings))
        if inspection.missing_finalized_sale_document_numbers:
            evidence.append(
                "future sale document numbering has "
                f"{inspection.missing_finalized_sale_document_numbers} finalized sale(s) missing document_number"
            )
        elif present_settings == SALE_DOCUMENT_SETTING_KEYS:
            evidence.append("future sale document numbering data requirements")

    if OPTIONAL_SHIFTS_REVISION in later_revisions:
        for table, columns in OPTIONAL_SHIFTS_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[OPTIONAL_SHIFTS_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))
        present_settings = OPTIONAL_SHIFTS_SETTING_KEYS & inspection.settings_keys
        evidence.extend(f"future setting {key}" for key in sorted(present_settings))
        for table, columns in NULLABLE_COLUMNS_BY_REVISION[OPTIONAL_SHIFTS_REVISION].items():
            nullable = inspection.nullable_columns_by_table.get(table, set()) & columns
            evidence.extend(f"future nullable column {table}.{column}" for column in sorted(nullable))

    if SHIFTLESS_REFUNDS_REVISION in later_revisions:
        for table, columns in SHIFTLESS_REFUNDS_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[SHIFTLESS_REFUNDS_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))
        for table, columns in NULLABLE_COLUMNS_BY_REVISION[SHIFTLESS_REFUNDS_REVISION].items():
            nullable = inspection.nullable_columns_by_table.get(table, set()) & columns
            evidence.extend(f"future nullable column {table}.{column}" for column in sorted(nullable))

    if QUICK_SALE_CUSTOMER_REVISION in later_revisions:
        for table, columns in QUICK_SALE_CUSTOMER_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[QUICK_SALE_CUSTOMER_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))

    if DOCUMENT_WORKFLOW_REVISION in later_revisions:
        for table, columns in DOCUMENT_WORKFLOW_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))
        present_indexes = REQUIRED_INDEXES_BY_REVISION[DOCUMENT_WORKFLOW_REVISION] & inspection.indexes
        evidence.extend(f"future index {index}" for index in sorted(present_indexes))

    if CUSTOMER_DISCOUNT_REVISION in later_revisions:
        for table, columns in CUSTOMER_DISCOUNT_COLUMNS.items():
            present = inspection.columns_by_table.get(table, set()) & columns
            evidence.extend(f"future column {table}.{column}" for column in sorted(present))

    return evidence


def classify_schema(inspection: SchemaInspection) -> SchemaClassification:
    if not inspection.sqlite:
        return SchemaClassification(CLASS_UNKNOWN, None, "Safe legacy stamping only supports SQLite databases.")

    user_tables = inspection.tables - {"alembic_version"}
    if not user_tables:
        return SchemaClassification(CLASS_EMPTY, None, "No application tables were found.")

    unexpected = _unexpected_schema(inspection)
    if unexpected:
        return SchemaClassification(
            CLASS_UNKNOWN,
            None,
            "Schema contains unknown objects that do not match the known Alembic model.",
            unexpected=tuple(unexpected),
        )

    candidates = [
        (CUSTOMER_DISCOUNT_REVISION, CUSTOMER_DISCOUNT_SCHEMA),
        (DOCUMENT_WORKFLOW_REVISION, DOCUMENT_WORKFLOW_SCHEMA),
        (QUICK_SALE_CUSTOMER_REVISION, QUICK_SALE_CUSTOMER_SCHEMA),
        (SHIFTLESS_REFUNDS_REVISION, SHIFTLESS_REFUNDS_SCHEMA),
        (OPTIONAL_SHIFTS_REVISION, OPTIONAL_SHIFTS_SCHEMA),
        (SALE_DOCUMENT_REVISION, SALE_DOCUMENT_SCHEMA),
        (INVOICE_FOLLOWUP_REVISION, INVOICE_FOLLOWUP_SCHEMA),
        (UNIFIED_SALES_REVISION, UNIFIED_SALES_SCHEMA),
        (STABILIZATION_REVISION, STABILIZATION_SCHEMA),
        (INVENTORY_REVISION, INVENTORY_SCHEMA),
        (AUTH_REVISION, AUTH_SCHEMA),
        (BASELINE_REVISION, BASELINE_SCHEMA),
    ]
    failures: list[str] = []
    for revision, schema in candidates:
        missing = _missing_schema(schema, inspection)
        missing.extend(_missing_indexes(revision, inspection))
        missing.extend(_missing_nullable_requirements(revision, inspection))
        missing.extend(_missing_triggers(revision, inspection))
        missing.extend(_missing_data_requirements(revision, inspection))
        if not missing:
            future_evidence = _future_revision_evidence(revision, inspection)
            if future_evidence:
                return SchemaClassification(
                    CLASS_UNKNOWN,
                    None,
                    "Schema contains objects from later revisions but does not fully match those revisions.",
                    unexpected=tuple(future_evidence),
                )
            return SchemaClassification(
                REVISION_LABELS[revision],
                revision,
                f"Schema satisfies all critical checks for revision {revision}.",
            )
        failures.extend(missing)

    return SchemaClassification(
        CLASS_UNKNOWN,
        None,
        "Schema is partial or does not match a known revision.",
        missing=tuple(sorted(set(failures))),
    )


def expected_backup_path(database_path: Path, backup_dir: Path, now: datetime | None = None) -> Path:
    timestamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    return backup_dir / f"{database_path.stem}-migration-{timestamp}{database_path.suffix or '.sqlite'}"


def quick_check(database_path: Path) -> bool:
    with _connect_sqlite(database_path) as connection:
        result = connection.execute("PRAGMA quick_check").fetchone()
    return bool(result and result[0] == "ok")


def create_sqlite_backup(database_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_path = expected_backup_path(database_path, backup_dir)
    counter = 1
    while backup_path.exists():
        backup_path = backup_path.with_name(f"{backup_path.stem}-{counter}{backup_path.suffix}")
        counter += 1

    with _connect_sqlite(database_path) as source, _connect_sqlite(backup_path) as target:
        source.backup(target)

    if not quick_check(backup_path):
        try:
            backup_path.unlink()
        except OSError:
            pass
        raise MigrationBootstrapError(f"Migration backup failed PRAGMA quick_check: {backup_path}")
    return backup_path


def _alembic_config(database_url: str) -> Config:
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def plan_bootstrap(database_url: str, *, backup_dir: str | Path | None = None, dry_run: bool = False) -> BootstrapPlan:
    inspection = inspect_database(database_url)
    classification = classify_schema(inspection)
    settings = get_settings()
    selected_backup_dir = Path(backup_dir or settings.backup_dir) / "migration-backups"

    backup_path: Path | None = None
    stamp_revision: str | None = None
    upgrade_target: str | None = None
    actions: list[str] = []

    if not inspection.sqlite:
        actions.append("run alembic upgrade head")
        return BootstrapPlan(inspection, classification, dry_run, None, None, "head", tuple(actions))

    if inspection.alembic_versions:
        actions.append(f"database already stamped: {', '.join(inspection.alembic_versions)}")
        if classification.classification == CLASS_UNKNOWN:
            actions.append("abort because stamped revision and schema contents are inconsistent")
            return BootstrapPlan(inspection, classification, dry_run, None, None, None, tuple(actions))
        if inspection.alembic_versions != (HEAD_REVISION,):
            upgrade_target = "head"
            actions.append("run alembic upgrade head")
            if inspection.database_path and (inspection.tables - {"alembic_version"}):
                backup_path = expected_backup_path(inspection.database_path, selected_backup_dir)
                actions.insert(1, f"create migration backup: {backup_path}")
        else:
            actions.append("no migration required")
        return BootstrapPlan(inspection, classification, dry_run, backup_path, None, upgrade_target, tuple(actions))

    if classification.classification == CLASS_EMPTY:
        upgrade_target = "head"
        actions.append("run alembic upgrade head")
    elif classification.matched_revision in REVISION_ORDER:
        stamp_revision = classification.matched_revision
        actions.append(f"stamp {classification.matched_revision}")
        if classification.matched_revision != HEAD_REVISION:
            upgrade_target = "head"
            actions.append("run alembic upgrade head")
    else:
        actions.append("abort without stamping or upgrading")

    if (
        inspection.database_path
        and (inspection.tables - {"alembic_version"})
        and (stamp_revision or upgrade_target)
    ):
        backup_path = expected_backup_path(inspection.database_path, selected_backup_dir)
        actions.insert(0, f"create migration backup: {backup_path}")

    return BootstrapPlan(inspection, classification, dry_run, backup_path, stamp_revision, upgrade_target, tuple(actions))


def run_bootstrap(database_url: str, *, backup_dir: str | Path | None = None, dry_run: bool = False) -> BootstrapPlan:
    plan = plan_bootstrap(database_url, backup_dir=backup_dir, dry_run=dry_run)
    classification = plan.classification

    if dry_run:
        return plan

    if classification.classification == CLASS_UNKNOWN:
        details = "\n".join(classification.missing or classification.unexpected or ())
        raise MigrationBootstrapError(
            "Database schema is not safely recognizable. No migration was run.\n"
            "Create a manual SQLite backup, inspect the schema, and do not use 'alembic stamp head' "
            "unless the full current schema is confirmed.\n"
            f"{details}"
        )

    if plan.backup_path and plan.inspection.database_path:
        actual_backup = create_sqlite_backup(plan.inspection.database_path, plan.backup_path.parent)
        plan = BootstrapPlan(
            plan.inspection,
            plan.classification,
            plan.dry_run,
            actual_backup,
            plan.stamp_revision,
            plan.upgrade_target,
            plan.actions,
        )

    config = _alembic_config(database_url)
    if plan.stamp_revision:
        command.stamp(config, plan.stamp_revision)
    if plan.upgrade_target:
        command.upgrade(config, plan.upgrade_target)
    return plan


def _format_items(items: Iterable[str]) -> str:
    return "\n".join(f"  - {item}" for item in items)


def format_plan(plan: BootstrapPlan) -> str:
    lines = [
        f"Database URL: {plan.inspection.database_url}",
        f"Database path: {plan.inspection.database_path or 'not a filesystem SQLite database'}",
        f"Alembic revision(s): {', '.join(plan.inspection.alembic_versions) or 'none'}",
        f"Schema classification: {plan.classification.classification}",
        f"Matched revision: {plan.classification.matched_revision or 'none'}",
        f"Reason: {plan.classification.reason}",
        f"Backup path: {plan.backup_path or 'none'}",
        f"Stamp decision: {plan.stamp_revision or 'none'}",
        f"Upgrade target: {plan.upgrade_target or 'none'}",
        f"Dry run: {'yes' if plan.dry_run else 'no'}",
        "Planned actions:",
        _format_items(plan.actions),
    ]
    if plan.classification.missing:
        lines.append("Missing schema details:")
        lines.append(_format_items(plan.classification.missing[:50]))
    if plan.classification.unexpected:
        lines.append("Unexpected schema details:")
        lines.append(_format_items(plan.classification.unexpected[:50]))
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Safely bootstrap Alembic for legacy SQLite databases.")
    parser.add_argument("--database-url", default=None, help="Database URL. Defaults to application settings.")
    parser.add_argument("--backup-dir", default=None, help="Backup root. Defaults to application backup_dir.")
    parser.add_argument("--dry-run", action="store_true", help="Inspect and plan without modifying the database.")
    args = parser.parse_args()

    settings = get_settings()
    database_url = args.database_url or settings.database_url
    try:
        plan = run_bootstrap(database_url, backup_dir=args.backup_dir, dry_run=args.dry_run)
    except MigrationBootstrapError as exc:
        print(str(exc))
        return 2
    except Exception as exc:
        print(f"Migration bootstrap failed: {exc}")
        return 1

    print(format_plan(plan))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
