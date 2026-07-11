from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


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
    user_columns = {column["name"] for column in inspector.get_columns("users")}
    assert "password_hash" in user_columns
    sale_columns = {column["name"] for column in inspector.get_columns("sales")}
    assert {
        "sold_by_user_id",
        "created_by_user_id",
        "cash_register_id",
        "created_at",
        "seller_override_reason",
        "seller_overridden_by_user_id",
        "seller_overridden_at",
    }.issubset(sale_columns)

    shift_indexes = {index["name"] for index in inspector.get_indexes("shifts")}
    assert "ux_open_shift_seller" in shift_indexes
    assert "ux_open_shift_register" in shift_indexes

    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == "8d4b2f3a91c7"
