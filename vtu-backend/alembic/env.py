from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.db import Base
import app.models  # noqa: F401  (registers tables)

config = context.config
config.set_main_option("sqlalchemy.url", settings.database_url)
target_metadata = Base.metadata


def run_migrations_offline():
    """`alembic upgrade head --sql`: print the SQL instead of connecting to a database."""
    context.configure(url=settings.database_url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()


def run_on(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    # Tests pass in an already-verified connection to the test database; normal use builds one from settings.
    connection = config.attributes.get("connection")
    if connection is not None:
        run_on(connection)
        return
    connectable = engine_from_config(config.get_section(config.config_ini_section), prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        run_on(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
