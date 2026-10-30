"""Guards that keep the test suite away from every database except the throw-away Docker test database.

Tests create and drop a database, so every check here fails closed: anything unexpected stops the run.
"""
import re

from sqlalchemy.engine import URL

# The project's Docker PostgreSQL only: its published port as seen from the Mac, or the Compose service from inside
# a container. Port 5432 on the Mac is the separate Homebrew server and is deliberately absent.
ALLOWED_SERVERS = frozenset({("localhost", 5433), ("127.0.0.1", 5433), ("db", 5432)})
# Never created, dropped or migrated by tests, whatever the configuration says.
PROTECTED_DATABASES = frozenset({"vtu_ledger", "postgres", "template0", "template1"})
EXPECTED_HEAD = "v2_0001"
_TEST_DB_NAME = re.compile(r"[a-z][a-z0-9_]*_test")


class UnsafeTestDatabase(Exception):
    pass


def check_server(url: URL) -> None:
    """The URL must point at the project's Docker PostgreSQL."""
    if url.get_backend_name() != "postgresql":
        raise UnsafeTestDatabase(f"not a PostgreSQL URL: {url.get_backend_name()}")
    server = (url.host, url.port or 5432)          # libpq's default port is 5432
    if server not in ALLOWED_SERVERS:
        raise UnsafeTestDatabase(f"server {server[0]}:{server[1]} is not the Docker database "
                                 f"(allowed: localhost:5433, 127.0.0.1:5433, db:5432)")


def check_test_url(url: URL) -> None:
    """The URL must be the Docker server AND a database named *_test that is not protected."""
    check_server(url)
    name = url.database or ""
    if name in PROTECTED_DATABASES or not _TEST_DB_NAME.fullmatch(name):
        raise UnsafeTestDatabase(f"database {name!r} is not a test database (must end in _test, "
                                 f"must not be one of {sorted(PROTECTED_DATABASES)})")


def derive_test_url(app_url: URL) -> URL:
    """The test database for the configured app database: same server, name + '_test'."""
    check_server(app_url)
    if app_url.database in (None, "") or app_url.database.endswith("_test"):
        raise UnsafeTestDatabase(f"configured database {app_url.database!r} must be the app database, "
                                 f"not a test database")
    url = app_url.set(database=f"{app_url.database}_test")
    check_test_url(url)
    return url


def check_connected_database(connection, expected: str) -> None:
    """Ask the server which database this connection is really using."""
    actual = connection.exec_driver_sql("SELECT current_database()").scalar()
    if actual != expected:
        raise UnsafeTestDatabase(f"connected to {actual!r}, expected {expected!r}")


def check_head(script_directory) -> None:
    """Only the 14-table baseline history may be applied to the test database."""
    heads = list(script_directory.get_heads())
    if heads != [EXPECTED_HEAD]:
        raise UnsafeTestDatabase(f"Alembic head is {heads}, expected [{EXPECTED_HEAD!r}]")
