"""Unit tests for the test-database guards (tests/db_safety.py). No database needed."""
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy.engine import make_url

import app.db
from app.config import settings
from db_safety import (UnsafeTestDatabase, check_connected_database, check_head, check_server, check_test_url,
                       derive_test_url)

ROOT = Path(__file__).resolve().parent.parent
PG = "postgresql+psycopg://vtu:pw@"


@pytest.mark.parametrize("url", [
    f"{PG}localhost:5433/vtu_ledger_test",
    f"{PG}127.0.0.1:5433/vtu_ledger_test",
    f"{PG}db:5432/vtu_ledger_test",
    f"{PG}db/vtu_ledger_test",                       # no port = 5432, the Compose service
])
def test_accepts_only_the_docker_test_database(url):
    check_test_url(make_url(url))


@pytest.mark.parametrize("url, reason", [
    (f"{PG}localhost:5433/vtu_ledger", "the app database"),
    (f"{PG}db:5432/vtu_ledger", "the app database inside Compose"),
    (f"{PG}localhost:5433/postgres", "maintenance database"),
    (f"{PG}localhost:5433/template1", "template database"),
    (f"{PG}localhost:5433/vtu_ledger_testing", "does not end in _test"),
    (f"{PG}localhost:5433/Vtu_Ledger_Test", "unexpected characters"),
    (f"{PG}localhost:5432/vtu_ledger_test", "Homebrew PostgreSQL on the Mac"),
    (f"{PG}localhost/vtu_ledger_test", "no port = 5432 = Homebrew"),
    (f"{PG}127.0.0.1:5432/vtu_ledger_test", "Homebrew via 127.0.0.1"),
    (f"{PG}db:5433/vtu_ledger_test", "wrong port for the Compose service"),
    (f"{PG}example.com:5433/vtu_ledger_test", "some other server"),
    ("sqlite:///vtu_ledger_test", "not PostgreSQL"),
])
def test_rejects_everything_else(url, reason):
    with pytest.raises(UnsafeTestDatabase):
        check_test_url(make_url(url))


def test_derives_test_database_on_the_same_server():
    assert derive_test_url(make_url(f"{PG}db:5432/vtu_ledger")).render_as_string(False) == f"{PG}db:5432/vtu_ledger_test"
    with pytest.raises(UnsafeTestDatabase):                        # Homebrew server: refused before deriving
        derive_test_url(make_url(f"{PG}localhost:5432/vtu_ledger"))
    with pytest.raises(UnsafeTestDatabase):                        # configured URL is already a test database
        derive_test_url(make_url(f"{PG}db:5432/vtu_ledger_test"))


def test_admin_connection_target_is_checked():
    check_server(make_url(f"{PG}localhost:5433/postgres"))
    with pytest.raises(UnsafeTestDatabase):
        check_server(make_url(f"{PG}localhost:5432/postgres"))


def test_conftest_points_the_app_engine_at_the_test_database():
    assert app.db.engine.url.database == "vtu_ledger_test"
    assert make_url(settings.database_url).database == "vtu_ledger_test"
    check_test_url(app.db.engine.url)


def test_alembic_head_is_the_new_baseline():
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    check_head(ScriptDirectory.from_config(cfg))


def test_head_check_rejects_other_histories():
    class Fake:
        def __init__(self, heads): self.heads = heads
        def get_heads(self): return self.heads
    for heads in (["0002"], ["v2_0001", "0002"], []):
        with pytest.raises(UnsafeTestDatabase):
            check_head(Fake(heads))


def test_connected_database_check():
    class FakeConnection:
        def __init__(self, name): self.name = name
        def exec_driver_sql(self, sql):
            assert sql == "SELECT current_database()"
            return type("R", (), {"scalar": lambda _: self.name})()
    check_connected_database(FakeConnection("vtu_ledger_test"), "vtu_ledger_test")
    with pytest.raises(UnsafeTestDatabase):
        check_connected_database(FakeConnection("vtu_ledger"), "vtu_ledger_test")
