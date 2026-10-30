from contextlib import contextmanager

from fastapi import HTTPException
from sqlalchemy import MetaData, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# Predictable constraint names (e.g. ck_student_current_semester). The baseline migration spells out the same
# names, and tests/test_schema_offline.py checks that the models and the migration produce identical DDL.
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def get_db():
    with SessionLocal() as session:
        yield session


@contextmanager
def write_transaction(db):
    """Commit the block's changes together or not at all. A constraint violation (for example the one-ACTIVE-award
    index losing a race with a concurrent commit) becomes a 409 without exposing database internals."""
    try:
        yield
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "This change conflicts with data saved in the meantime. Reload and try again.")
    except BaseException:
        db.rollback()
        raise
