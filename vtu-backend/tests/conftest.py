"""Tests run against a throw-away database (<name>_test) so they can never touch real data."""
import os
from pathlib import Path

import psycopg
import pytest
from sqlalchemy import delete, select, update
from sqlalchemy.engine import make_url

from app.config import settings

_url = make_url(os.environ.get("DATABASE_URL") or settings.database_url)
_test_db = _url.database + "_test"
settings.database_url = _url.set(database=_test_db).render_as_string(hide_password=False)   # BEFORE app.db is imported

with psycopg.connect(host=_url.host, port=_url.port, user=_url.username, password=_url.password, dbname="postgres", autocommit=True) as admin:
    admin.execute(f'DROP DATABASE IF EXISTS "{_test_db}" WITH (FORCE)')
    admin.execute(f'CREATE DATABASE "{_test_db}"')

from alembic import command                      # noqa: E402
from alembic.config import Config                # noqa: E402

_root = Path(__file__).resolve().parent.parent
_cfg = Config(str(_root / "alembic.ini")); _cfg.set_main_option("script_location", str(_root / "alembic"))
command.upgrade(_cfg, "head")

from app import ratelimit                        # noqa: E402
from app.db import SessionLocal                  # noqa: E402
from app.models import Event, PointLedger, PointRequest, User   # noqa: E402
from scripts.seed import run as seed             # noqa: E402

seed()


@pytest.fixture(autouse=True)
def clean_state():
    """Snapshot before each test, restore after, so tests are independent of order."""
    ratelimit.clear()
    with SessionLocal() as db:
        ledger = {l.id: l.status for l in db.scalars(select(PointLedger))}
        events = set(db.scalars(select(Event.id)))
        users = {u.id: u.is_active for u in db.scalars(select(User))}
    yield
    with SessionLocal() as db:
        db.execute(delete(PointLedger).where(PointLedger.id.not_in(ledger)))
        for i, st in ledger.items():
            db.execute(update(PointLedger).where(PointLedger.id == i).values(status=st))
        db.execute(delete(PointRequest))
        db.execute(delete(Event).where(Event.id.not_in(events)))
        for i, a in users.items():
            db.execute(update(User).where(User.id == i).values(is_active=a))
        db.commit()
