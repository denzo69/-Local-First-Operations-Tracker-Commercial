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
    CLASS_STABILIZATION,
    CLASS_UNKNOWN,
    HEAD_REVISION,
    INVENTORY_REVISION,
    STABILIZATION_REVISION,
    MigrationBootstrapError,
    classify_schema,
    inspect_database,
    quick_check,
    run_bootstrap,
    sqlite_path_from_url,
)


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
    payment_columns = {column["name"] for column in inspector.get_columns("payments")}
    assert "received_by_user_id" in payment_columns
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
    assert version == "9e4c3b2a1f08"


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
    assert plan.upgrade_target is None
    assert before_tables | {"alembic_version"} == after_tables
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

    assert classification.classification == CLASS_STABILIZATION
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
        CLASS_UNKNOWN,
    }
