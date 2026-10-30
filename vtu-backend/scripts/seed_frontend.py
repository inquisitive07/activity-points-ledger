"""Seed the isolated frontend integration database (vtu_ledger_frontend) with the demo data from scripts/seed.py.

Run only in the api-frontend container (docker-compose.frontend.yml):

    docker compose -f docker-compose.yml -f docker-compose.frontend.yml exec api-frontend python scripts/seed_frontend.py

Fails closed: nothing is written unless both the configured URL and the live connection (SELECT current_database())
name vtu_ledger_frontend. The seed itself is insert-only and idempotent (see scripts/seed.py).
"""
import sys

from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.config import settings
from app.db import SessionLocal
from scripts import seed

EXPECTED = "vtu_ledger_frontend"


def main():
    configured = make_url(settings.database_url).database
    if configured != EXPECTED:
        sys.exit(f"Refusing to seed: DATABASE_URL names {configured!r}, expected {EXPECTED!r}.")
    with SessionLocal() as db:
        actual = db.execute(text("SELECT current_database()")).scalar()
    if actual != EXPECTED:
        sys.exit(f"Refusing to seed: connected to {actual!r}, expected {EXPECTED!r}.")
    print(f"current_database() = {actual}; storage = {settings.storage_dir}")
    seed.run()


if __name__ == "__main__":
    main()
