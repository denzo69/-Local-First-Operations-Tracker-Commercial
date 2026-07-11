from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.database import Base
import app.models  # noqa: F401
from app.services.migration_service import ensure_sqlite_schema_compatibility


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

    shift_indexes = {index["name"] for index in inspector.get_indexes("shifts")}
    assert "ux_open_shift_seller" in shift_indexes
    assert "ux_open_shift_register" in shift_indexes

    with engine.connect() as connection:
        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()

    assert version == "3f0d1c9a8b22"


def test_pre_password_hash_database_upgrades_to_head(tmp_path):
    db_path = tmp_path / "pre_password.sqlite"
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path.as_posix()}")

    command.upgrade(config, "162323fcac91")
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    before_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    assert "password_hash" not in before_columns

    command.upgrade(config, "head")

    after_columns = {column["name"] for column in inspect(engine).get_columns("users")}
    assert "password_hash" in after_columns


def test_current_schema_compatibility_shim_is_noop_for_row_counts(tmp_path):
    db_path = tmp_path / "current.sqlite"
    engine = create_engine(f"sqlite:///{db_path.as_posix()}", future=True)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO customers (name, created_at, updated_at) VALUES ('Existing', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)")
        )
        before_count = connection.execute(text("SELECT COUNT(*) FROM customers")).scalar_one()

    diagnostics = ensure_sqlite_schema_compatibility(engine)

    with engine.connect() as connection:
        after_count = connection.execute(text("SELECT COUNT(*) FROM customers")).scalar_one()

    assert diagnostics == []
    assert after_count == before_count
