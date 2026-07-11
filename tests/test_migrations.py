from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


def test_windows_run_scripts_apply_alembic_before_uvicorn():
    for script_name in ["run.bat", "run-lan.bat"]:
        script = open(script_name, encoding="utf-8").read().lower()
        alembic_index = script.index("python -m alembic upgrade head")
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
    product_columns = {column["name"] for column in inspector.get_columns("products")}
    assert "current_weighted_average_cost_ex_vat" in product_columns
    assert "current_inventory_quantity" in product_columns
    assert "current_inventory_value_ex_vat" in product_columns
    sale_columns = {column["name"] for column in inspector.get_columns("sales")}
    assert "cost_of_goods_sold_ex_vat" in sale_columns
    assert "gross_profit_ex_vat" in sale_columns
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

    assert version == "7c2a91f4d8e3"
