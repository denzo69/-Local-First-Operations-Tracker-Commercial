from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.migration_bootstrap import (
    AUTH_REVISION,
    BASELINE_REVISION,
    CLASS_AUTH,
    CLASS_BASELINE,
    CLASS_EMPTY,
    CLASS_INVENTORY,
    CLASS_INVOICE_FOLLOWUP,
    CLASS_OPTIONAL_SHIFTS,
    CLASS_SALE_DOCUMENTS,
    CLASS_SHIFTLESS_REFUNDS,
    CLASS_STABILIZATION,
    CLASS_UNKNOWN,
    CLASS_UNIFIED_SALES,
    HEAD_REVISION,
    INVOICE_FOLLOWUP_REVISION,
    INVENTORY_REVISION,
    OPTIONAL_SHIFTS_REVISION,
    SALE_DOCUMENT_REVISION,
    SHIFTLESS_REFUNDS_REVISION,
    STABILIZATION_REVISION,
    UNIFIED_SALES_REVISION,
    MigrationBootstrapError,
    classify_schema,
    inspect_database,
    quick_check,
    run_bootstrap,
    sqlite_path_from_url,
)
from app.services.migration_service import ensure_sqlite_schema_compatibility


def _database_url(db_path):
    return f"sqlite:///{db_path.as_posix()}"


def _alembic_config(db_path):
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", _database_url(db_path))
    return config


def _upgrade_to_revision(db_path, revision):
    command.upgrade(_alembic_config(db_path), revision)


def _drop_alembic_version(db_path):
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE alembic_version"))
    engine.dispose()


def _current_revision(db_path):
    engine = create_engine(_database_url(db_path), future=True)
    with engine.connect() as connection:
        revision = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    engine.dispose()
    return revision


def test_sqlite_relative_url_resolves_inside_current_working_directory():
    path = sqlite_path_from_url("sqlite:///./data/app.sqlite")

    assert path is not None
    assert path == (path.__class__.cwd() / "data" / "app.sqlite").resolve()


def test_windows_run_scripts_apply_alembic_before_uvicorn():
    for script_name in ["run.bat", "run-lan.bat"]:
        script = open(script_name, encoding="utf-8").read().lower()
        alembic_index = script.index("python -m app.migration_bootstrap")
        uvicorn_index = script.index("-m uvicorn")

        assert alembic_index < uvicorn_index


def test_alembic_upgrade_head_creates_current_schema(tmp_path):
    db_path = tmp_path / "alembic.sqlite"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())

    assert "alembic_version" in tables
    assert "customers" in tables
    assert "jobs" in tables
    assert "sales" in tables
    assert "refunds" in tables
    assert "daily_closing_snapshots" in tables
    assert "suppliers" in tables
    assert "warehouses" in tables
    assert "warehouse_locations" in tables
    assert "inventory_balances" in tables
    assert "goods_receipts" in tables
    assert "goods_receipt_lines" in tables
    assert "inventory_transactions" in tables
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert "password_hash" in user_columns
    assert "can_receive_sales_credit" in user_columns
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    assert "current_weighted_average_cost_ex_vat" in product_columns
    assert "current_inventory_quantity" in product_columns
    assert "current_inventory_value_ex_vat" in product_columns
    sale_columns = {column["name"] for column in inspector.get_columns("sales")}
    assert "cost_of_goods_sold_ex_vat" in sale_columns
    assert "gross_profit_ex_vat" in sale_columns
    assert "sold_by_user_id" in sale_columns
    assert "created_by_user_id" in sale_columns
    assert "cash_register_id" in sale_columns
    assert "source_type" in sale_columns
    assert "idempotency_key" in sale_columns
    assert "settlement_status" in sale_columns
    assert "finalized_at" in sale_columns
    assert "invoice_customer_snapshot_json" in sale_columns
    assert "transferred_to_invoicing_at" in sale_columns
    assert "external_invoice_service" in sale_columns
    assert "external_invoice_number" in sale_columns
    assert "invoice_date" in sale_columns
    assert "due_date" in sale_columns
    assert "payment_status_checked_at" in sale_columns
    assert "paid_at" in sale_columns
    assert "next_follow_up_at" in sale_columns
    assert "reminder_count" in sale_columns
    assert "last_reminder_sent_at" in sale_columns
    assert "follow_up_notes" in sale_columns
    assert "business_date" in sale_columns
    payment_columns = {column["name"] for column in inspector.get_columns("payments")}
    assert "received_by_user_id" in payment_columns
    sale_column_meta = {column["name"]: column for column in inspector.get_columns("sales")}
    payment_column_meta = {column["name"]: column for column in inspector.get_columns("payments")}
    assert sale_column_meta["shift_id"]["nullable"] is True
    assert sale_column_meta["seller_id"]["nullable"] is True
    assert sale_column_meta["business_date"]["nullable"] is True
    assert payment_column_meta["shift_id"]["nullable"] is True
    assert payment_column_meta["seller_id"]["nullable"] is True
    receipt_columns = {column["name"] for column in inspector.get_columns("goods_receipts")}
    assert "freight_vat_rate" in receipt_columns
    assert "freight_vat_amount" in receipt_columns
    assert "freight_total_inc_vat" in receipt_columns
    assert "other_costs_vat_rate" in receipt_columns
    assert "other_costs_vat_amount" in receipt_columns
    assert "other_costs_total_inc_vat" in receipt_columns
    transaction_columns = {column["name"] for column in inspector.get_columns("inventory_transactions")}
    assert "quantity_change" in transaction_columns
    assert "inventory_value_before" in transaction_columns
    assert "inventory_value_after" in transaction_columns
    assert "weighted_average_cost_after" in transaction_columns

    shift_indexes = {index["name"] for index in inspector.get_indexes("shifts")}
    assert "ux_open_shift_seller" in shift_indexes
    assert "ux_open_shift_register" in shift_indexes

    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
        triggers = {
            row[0]
            for row in connection.execute(
                text("SELECT name FROM sqlite_master WHERE type = 'trigger'")
            ).fetchall()
        }

    assert "trg_inventory_transactions_no_update" in triggers
    assert "trg_inventory_transactions_no_delete" in triggers
    sale_indexes = {index["name"] for index in inspector.get_indexes("sales")}
    assert "ix_sales_settlement_status" in sale_indexes
    assert "ux_sales_active_work_order" in sale_indexes
    assert "ix_sales_due_date" in sale_indexes
    assert "ix_sales_next_follow_up_at" in sale_indexes
    assert "ix_sales_business_date" in sale_indexes
    assert version == HEAD_REVISION


def test_empty_database_bootstrap_upgrades_to_head(tmp_path):
    db_path = tmp_path / "empty.sqlite"

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_EMPTY
    assert plan.backup_path is None
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_baseline_database_is_stamped_and_upgraded(tmp_path):
    db_path = tmp_path / "baseline.sqlite"
    _upgrade_to_revision(db_path, BASELINE_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_BASELINE
    assert plan.stamp_revision == BASELINE_REVISION
    assert plan.upgrade_target == "head"
    assert plan.backup_path and plan.backup_path.exists()
    assert quick_check(plan.backup_path)
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_auth_database_is_stamped_and_upgraded(tmp_path):
    db_path = tmp_path / "auth.sqlite"
    _upgrade_to_revision(db_path, AUTH_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_AUTH
    assert plan.stamp_revision == AUTH_REVISION
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_inventory_database_is_stamped_and_upgraded(tmp_path):
    db_path = tmp_path / "inventory.sqlite"
    _upgrade_to_revision(db_path, INVENTORY_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_INVENTORY
    assert plan.stamp_revision == INVENTORY_REVISION
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_stabilization_database_is_stamped_without_rebuild(tmp_path):
    db_path = tmp_path / "stabilization.sqlite"
    _upgrade_to_revision(db_path, STABILIZATION_REVISION)
    _drop_alembic_version(db_path)
    before_tables = set(inspect(create_engine(_database_url(db_path), future=True)).get_table_names())

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    after_tables = set(inspect(create_engine(_database_url(db_path), future=True)).get_table_names())

    assert plan.classification.classification == CLASS_STABILIZATION
    assert plan.stamp_revision == STABILIZATION_REVISION
    assert plan.upgrade_target == "head"
    assert before_tables | {"alembic_version"} == after_tables
    assert _current_revision(db_path) == HEAD_REVISION


def test_sqlite_compatibility_adds_later_sales_columns_to_stabilized_database(tmp_path):
    db_path = tmp_path / "stabilization-compatibility.sqlite"
    _upgrade_to_revision(db_path, STABILIZATION_REVISION)
    engine = create_engine(_database_url(db_path), future=True)

    diagnostics = ensure_sqlite_schema_compatibility(engine)

    inspector = inspect(engine)
    sales_columns = {column["name"] for column in inspector.get_columns("sales")}
    sale_line_columns = {column["name"] for column in inspector.get_columns("sale_lines")}
    index_names = {index["name"] for index in inspector.get_indexes("sales")}
    engine.dispose()

    assert diagnostics == []
    assert {
        "source_type",
        "idempotency_key",
        "finalized_at",
        "settlement_status",
        "due_date",
        "next_follow_up_at",
        "reminder_count",
        "cost_of_goods_sold_ex_vat",
        "gross_profit_ex_vat",
        "gross_margin_percent",
        "business_date",
    }.issubset(sales_columns)
    assert {
        "cost_of_goods_sold_ex_vat",
        "gross_profit_ex_vat",
        "gross_margin_percent",
    }.issubset(sale_line_columns)
    assert {
        "ix_sales_source_type",
        "ix_sales_due_date",
        "ix_sales_next_follow_up_at",
        "ix_sales_business_date",
        "ux_sales_active_work_order",
    }.issubset(index_names)


def test_unstamped_unified_sales_database_is_stamped_and_upgraded(tmp_path):
    db_path = tmp_path / "unified-sales.sqlite"
    _upgrade_to_revision(db_path, UNIFIED_SALES_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_UNIFIED_SALES
    assert plan.stamp_revision == UNIFIED_SALES_REVISION
    assert plan.upgrade_target == "head"
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_invoice_followup_database_is_stamped_and_upgraded(tmp_path):
    db_path = tmp_path / "invoice-followup.sqlite"
    _upgrade_to_revision(db_path, INVOICE_FOLLOWUP_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_INVOICE_FOLLOWUP
    assert plan.stamp_revision == INVOICE_FOLLOWUP_REVISION
    assert plan.upgrade_target == "head"
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_sale_document_database_is_stamped_and_upgraded(tmp_path):
    db_path = tmp_path / "sale-documents.sqlite"
    _upgrade_to_revision(db_path, SALE_DOCUMENT_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_SALE_DOCUMENTS
    assert plan.stamp_revision == SALE_DOCUMENT_REVISION
    assert plan.upgrade_target == "head"
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_optional_shifts_database_is_stamped_and_upgraded(tmp_path):
    db_path = tmp_path / "optional-shifts.sqlite"
    _upgrade_to_revision(db_path, OPTIONAL_SHIFTS_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_OPTIONAL_SHIFTS
    assert plan.stamp_revision == OPTIONAL_SHIFTS_REVISION
    assert plan.upgrade_target == "head"
    assert _current_revision(db_path) == HEAD_REVISION


def test_unstamped_shiftless_refunds_database_is_stamped_without_upgrade(tmp_path):
    db_path = tmp_path / "shiftless-refunds.sqlite"
    _upgrade_to_revision(db_path, SHIFTLESS_REFUNDS_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.classification.classification == CLASS_SHIFTLESS_REFUNDS
    assert plan.stamp_revision == SHIFTLESS_REFUNDS_REVISION
    assert plan.upgrade_target is None
    assert _current_revision(db_path) == HEAD_REVISION


def test_stamped_pr19_revisions_upgrade_to_head(tmp_path):
    for revision in [
        STABILIZATION_REVISION,
        UNIFIED_SALES_REVISION,
        INVOICE_FOLLOWUP_REVISION,
        SALE_DOCUMENT_REVISION,
        OPTIONAL_SHIFTS_REVISION,
    ]:
        db_path = tmp_path / f"stamped-{revision}.sqlite"
        _upgrade_to_revision(db_path, revision)

        plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

        assert plan.inspection.alembic_versions == (revision,)
        assert plan.stamp_revision is None
        assert plan.upgrade_target == "head"
        assert _current_revision(db_path) == HEAD_REVISION


def test_already_stamped_old_revision_uses_normal_upgrade(tmp_path):
    db_path = tmp_path / "stamped-old.sqlite"
    _upgrade_to_revision(db_path, AUTH_REVISION)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.inspection.alembic_versions == (AUTH_REVISION,)
    assert plan.stamp_revision is None
    assert plan.upgrade_target == "head"
    assert _current_revision(db_path) == HEAD_REVISION


def test_already_stamped_head_is_noop(tmp_path):
    db_path = tmp_path / "stamped-head.sqlite"
    _upgrade_to_revision(db_path, HEAD_REVISION)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.inspection.alembic_versions == (HEAD_REVISION,)
    assert plan.stamp_revision is None
    assert plan.upgrade_target is None
    assert plan.backup_path is None
    assert _current_revision(db_path) == HEAD_REVISION


def test_partial_schema_aborts_without_stamp(tmp_path):
    db_path = tmp_path / "partial.sqlite"
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE audit_log (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    try:
        run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    except MigrationBootstrapError:
        pass
    else:
        raise AssertionError("Partial schema should abort")

    inspection = inspect_database(_database_url(db_path))
    assert "alembic_version" not in inspection.tables


def test_unknown_extra_critical_column_aborts_safely(tmp_path):
    db_path = tmp_path / "unknown-extra.sqlite"
    _upgrade_to_revision(db_path, HEAD_REVISION)
    _drop_alembic_version(db_path)
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE users ADD COLUMN mystery_column TEXT"))
    engine.dispose()

    inspection = inspect_database(_database_url(db_path))
    classification = classify_schema(inspection)

    assert classification.classification == CLASS_UNKNOWN
    assert "unexpected column users.mystery_column" in classification.unexpected

    try:
        run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    except MigrationBootstrapError:
        pass
    else:
        raise AssertionError("Unknown schema should abort")


def test_partial_future_revision_objects_prevent_lower_revision_classification(tmp_path):
    db_path = tmp_path / "partial-future.sqlite"
    _upgrade_to_revision(db_path, AUTH_REVISION)
    _drop_alembic_version(db_path)
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL)"))
    engine.dispose()

    inspection = inspect_database(_database_url(db_path))
    classification = classify_schema(inspection)

    assert classification.classification == CLASS_UNKNOWN
    assert "future table suppliers" in classification.unexpected

    try:
        run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    except MigrationBootstrapError:
        pass
    else:
        raise AssertionError("Partial future schema should abort")


def test_partial_invoice_followup_schema_aborts_safely(tmp_path):
    db_path = tmp_path / "partial-invoice-followup.sqlite"
    _upgrade_to_revision(db_path, UNIFIED_SALES_REVISION)
    _drop_alembic_version(db_path)
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE sales ADD COLUMN due_date DATE"))
    engine.dispose()

    classification = classify_schema(inspect_database(_database_url(db_path)))

    assert classification.classification == CLASS_UNKNOWN
    assert "future column sales.due_date" in classification.unexpected

    try:
        run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    except MigrationBootstrapError:
        pass
    else:
        raise AssertionError("Partial invoice follow-up schema should abort")


def test_sale_document_schema_with_missing_sale_numbers_aborts_safely(tmp_path):
    db_path = tmp_path / "missing-sale-documents.sqlite"
    _upgrade_to_revision(db_path, SALE_DOCUMENT_REVISION)
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("INSERT INTO roles (id, code, name, created_at) VALUES (501, 'seller', 'Seller', CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO users (id, name, login_name, is_active, role_id, created_at, updated_at, can_receive_sales_credit) VALUES (501, 'Seller', 'seller.test', 1, 501, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)"))
        connection.execute(text("INSERT INTO cash_registers (id, name, is_active, created_at, updated_at) VALUES (501, 'Register', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
        connection.execute(text("INSERT INTO shifts (id, cash_register_id, seller_id, opened_at, business_date, starting_cash, status) VALUES (501, 501, 501, CURRENT_TIMESTAMP, '2026-07-12', 0, 'open')"))
        connection.execute(
            text(
                """
                INSERT INTO sales (
                    id, seller_id, sold_by_user_id, created_by_user_id, shift_id, cash_register_id,
                    finalized_at, payment_method, settlement_status, subtotal, vat_total,
                    discount_total, total, status, sold_at, document_number
                )
                VALUES (
                    501, 501, 501, 501, 501, 501,
                    CURRENT_TIMESTAMP, 'cash', 'paid', 0, 0,
                    0, 0, 'completed', CURRENT_TIMESTAMP, NULL
                )
                """
            )
        )
        connection.execute(text("DROP TABLE alembic_version"))
    engine.dispose()

    classification = classify_schema(inspect_database(_database_url(db_path)))

    assert classification.classification == CLASS_UNKNOWN
    assert any(
        "1 finalized sale(s) missing document_number" in detail
        for detail in classification.missing + classification.unexpected
    )

    try:
        run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    except MigrationBootstrapError:
        pass
    else:
        raise AssertionError("Missing sale document numbers should abort")


def test_stamped_old_revision_with_partial_future_schema_aborts(tmp_path):
    db_path = tmp_path / "stamped-partial-future.sqlite"
    _upgrade_to_revision(db_path, AUTH_REVISION)
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE suppliers (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL)"))
    engine.dispose()

    try:
        run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    except MigrationBootstrapError:
        pass
    else:
        raise AssertionError("Stamped partial future schema should abort")

    assert _current_revision(db_path) == AUTH_REVISION


def test_extra_legacy_side_table_does_not_block_known_schema_classification(tmp_path):
    db_path = tmp_path / "extra-side-table.sqlite"
    _upgrade_to_revision(db_path, HEAD_REVISION)
    _drop_alembic_version(db_path)
    engine = create_engine(_database_url(db_path), future=True)
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE inventory_movements (id INTEGER PRIMARY KEY)"))
    engine.dispose()

    inspection = inspect_database(_database_url(db_path))
    classification = classify_schema(inspection)

    assert classification.classification == CLASS_SHIFTLESS_REFUNDS
    assert classification.matched_revision == HEAD_REVISION


def test_backup_created_and_quick_check_succeeds(tmp_path):
    db_path = tmp_path / "backup.sqlite"
    _upgrade_to_revision(db_path, BASELINE_REVISION)
    _drop_alembic_version(db_path)

    plan = run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")

    assert plan.backup_path
    assert plan.backup_path.exists()
    assert quick_check(plan.backup_path)


def test_failed_backup_validation_prevents_stamp_or_upgrade(tmp_path, monkeypatch):
    db_path = tmp_path / "bad-backup.sqlite"
    _upgrade_to_revision(db_path, BASELINE_REVISION)
    _drop_alembic_version(db_path)

    monkeypatch.setattr("app.migration_bootstrap.quick_check", lambda _path: False)

    try:
        run_bootstrap(_database_url(db_path), backup_dir=tmp_path / "backups")
    except MigrationBootstrapError:
        pass
    else:
        raise AssertionError("Failed backup validation should abort")

    inspection = inspect_database(_database_url(db_path))
    assert "alembic_version" not in inspection.tables


def test_default_database_can_be_classified_in_dry_run_without_modification():
    plan = run_bootstrap("sqlite:///./data/app.sqlite", dry_run=True)

    assert plan.dry_run is True
    assert plan.classification.classification in {
        CLASS_EMPTY,
        CLASS_BASELINE,
        CLASS_AUTH,
        CLASS_INVENTORY,
        CLASS_STABILIZATION,
        CLASS_UNIFIED_SALES,
        CLASS_INVOICE_FOLLOWUP,
        CLASS_SALE_DOCUMENTS,
        CLASS_OPTIONAL_SHIFTS,
        CLASS_UNKNOWN,
    }
