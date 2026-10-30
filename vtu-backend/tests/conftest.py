"""Test setup. Importing this file never connects to a database.

Tests that need PostgreSQL use the `db_engine` / `db` fixtures. The first such test creates a throw-away database,
<app database>_test (vtu_ledger_test), on the Docker server only, migrates it to v2_0001 and seeds it; it is dropped
again when the session ends. The app database (vtu_ledger) is never created, dropped, migrated or seeded by tests.
See tests/db_safety.py for the guards.
"""
import shutil
import tempfile
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url

from app.config import settings
from db_safety import (UnsafeTestDatabase, check_connected_database, check_head, check_server, check_test_url,
                       derive_test_url)

ROOT = Path(__file__).resolve().parent.parent

# 1. Work out the test database from the configured one and refuse anything that is not the Docker server.
try:
    TEST_URL = derive_test_url(make_url(settings.database_url))
except UnsafeTestDatabase as exc:
    raise pytest.UsageError(f"Refusing to run tests: {exc}") from None
TEST_DB = TEST_URL.database

# 2. Point the app at the test database BEFORE app.db creates its engine, then confirm the engine really uses it.
#    If anything had imported app.db earlier, its engine would still target the app database and this stops the run.
settings.database_url = TEST_URL.render_as_string(hide_password=False)
STORAGE_DIR = Path(tempfile.mkdtemp(prefix="vtu-test-storage-"))
settings.storage_dir = str(STORAGE_DIR)

import app.db  # noqa: E402  (must follow the override above)

try:
    check_test_url(app.db.engine.url)
    if app.db.engine.url.database != TEST_DB:
        raise UnsafeTestDatabase(f"app engine targets {app.db.engine.url.database!r}, expected {TEST_DB!r}")
except UnsafeTestDatabase as exc:
    raise pytest.UsageError(f"Refusing to run tests: {exc}") from None


def _alembic_config():
    from alembic.config import Config
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    return cfg


def _admin_execute(statement: str) -> None:
    """Run CREATE/DROP DATABASE for the test database only (from the Docker server's maintenance database)."""
    import psycopg
    from psycopg import sql

    url = app.db.engine.url
    check_test_url(url)                               # re-checked right before every create/drop
    check_server(url.set(database="postgres"))
    with psycopg.connect(host=url.host, port=url.port or 5432, user=url.username, password=url.password,
                         dbname="postgres", autocommit=True) as admin:
        admin.execute(sql.SQL(statement).format(db=sql.Identifier(url.database)))


@pytest.fixture(scope="session")
def db_engine():
    from alembic import command
    from alembic.script import ScriptDirectory

    cfg = _alembic_config()
    check_head(ScriptDirectory.from_config(cfg))      # only the v2_0001 baseline, never the old UUID history
    engine = app.db.engine
    _admin_execute("DROP DATABASE IF EXISTS {db} WITH (FORCE)")
    _admin_execute("CREATE DATABASE {db}")
    try:
        # Alembic runs on this verified connection (alembic/env.py uses config.attributes["connection"]),
        # so the migration cannot pick up a different URL.
        with engine.begin() as connection:
            check_connected_database(connection, TEST_DB)
            cfg.attributes["connection"] = connection
            command.upgrade(cfg, "head")
            check_connected_database(connection, TEST_DB)

        from scripts.seed import run as seed
        seed()
        yield engine
    finally:
        engine.dispose()
        _admin_execute("DROP DATABASE IF EXISTS {db} WITH (FORCE)")


@pytest.fixture
def db(db_engine):
    """A session whose changes are all rolled back after the test, so the seeded data stays unchanged."""
    from sqlalchemy.orm import Session

    connection = db_engine.connect()
    outer = connection.begin()                        # begin first: the check below runs a query
    check_connected_database(connection, TEST_DB)
    session = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield session
    session.close()
    outer.rollback()
    connection.close()


@pytest.fixture
def client(db):
    """An API client whose requests all run in the `db` session, so everything they write is rolled back.
    The endpoints' own commits only release a savepoint inside that session's outer transaction."""
    from fastapi.testclient import TestClient

    from app import ratelimit
    from app.db import get_db
    from app.main import app

    ratelimit.clear()
    app.dependency_overrides[get_db] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_db, None)
        ratelimit.clear()


def pytest_sessionfinish(session, exitstatus):
    """Remove the temporary storage folder, and only that folder."""
    if STORAGE_DIR.parent == Path(tempfile.gettempdir()) and STORAGE_DIR.name.startswith("vtu-test-storage-"):
        shutil.rmtree(STORAGE_DIR, ignore_errors=True)
