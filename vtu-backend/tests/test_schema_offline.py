"""Schema checks that need no database.

1. The models define exactly the 14 tables and 117 attributes of CLAUDE.md Section 5 / 18.1.
2. The baseline migration and the models produce the same DDL. The migration is rendered with Alembic's offline
   mode (`upgrade --sql`), the models with a mock engine; both are compiled by SQLAlchemy's PostgreSQL compiler,
   so any difference in a column, type, nullability, default, constraint or index shows up as a text difference.
"""
import io
import re
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_mock_engine

import app.models  # noqa: F401  (registers tables)
from app.db import Base
from app.models import student_point_summary

ROOT = Path(__file__).resolve().parent.parent

SPEC_ATTRIBUTE_COUNTS = {
    "department": 2, "student": 8, "user_account": 9, "point_requirement": 2, "club": 3, "event": 13,
    "document": 11, "ocr_batch": 12, "ocr_record": 12, "point_request": 17, "point_request_document": 3,
    "point_ledger": 12, "point_ledger_document": 4, "audit_log": 9,
}


def split_top_level(body: str) -> list[str]:
    """Split a CREATE TABLE body on commas that are not inside parentheses or quotes."""
    items, depth, quoted, cur = [], 0, False, ""
    for ch in body:
        if ch == "'":
            quoted = not quoted
        elif not quoted and ch == "(":
            depth += 1
        elif not quoted and ch == ")":
            depth -= 1
        if ch == "," and depth == 0 and not quoted:
            items.append(cur.strip()); cur = ""
        else:
            cur += ch
    items.append(cur.strip())
    return items


def normalise(statements: list[str]) -> dict:
    """Map each DDL statement to a comparable key/value. Column order inside a table does not matter."""
    out = {}
    for stmt in statements:
        s = " ".join(stmt.split()).rstrip(";").strip()
        if not s:
            continue
        m = re.match(r"CREATE TABLE (\w+) \((.*)\)$", s)
        if m:
            out[("table", m.group(1))] = sorted(split_top_level(m.group(2)))
        else:
            out[("stmt", s)] = True
    return out


def models_ddl() -> list[str]:
    out = []
    engine = create_mock_engine("postgresql+psycopg://", lambda sql, *a, **kw: out.append(str(sql.compile(dialect=engine.dialect))))
    Base.metadata.create_all(engine, checkfirst=False)
    return out


def migration_sql() -> str:
    buf = io.StringIO()
    cfg = Config(str(ROOT / "alembic.ini"), output_buffer=buf)
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.upgrade(cfg, "head", sql=True)
    return buf.getvalue()


NON_TABLE_PREFIXES = ("BEGIN", "COMMIT", "INSERT INTO alembic_version", "CREATE VIEW", "CREATE FUNCTION",
                      "CREATE TRIGGER", "BEGIN RAISE", "$$ LANGUAGE")


def migration_ddl(sql: str) -> list[str]:
    sql = "\n".join(line for line in sql.splitlines() if not line.startswith("--"))   # Alembic's progress comments
    stmts = [" ".join(s.split()) for s in sql.split(";\n")]
    return [s for s in stmts if s and not s.startswith(NON_TABLE_PREFIXES) and "alembic_version" not in s]


def test_exactly_14_tables_with_the_specified_attributes():
    counts = {name: len(table.columns) for name, table in Base.metadata.tables.items()}
    assert counts == SPEC_ATTRIBUTE_COUNTS
    assert sum(counts.values()) == 117


def test_view_is_not_registered_as_a_table():
    assert "student_point_summary" not in Base.metadata.tables


def test_migration_matches_models():
    from_models = normalise(models_ddl())
    from_migration = normalise(migration_ddl(migration_sql()))
    only_models = {k: v for k, v in from_models.items() if from_migration.get(k) != v}
    only_migration = {k: v for k, v in from_migration.items() if from_models.get(k) != v}
    assert not only_models and not only_migration, f"\nmodels only: {only_models}\nmigration only: {only_migration}"


def test_migration_creates_view_trigger_and_no_enum_types():
    sql = migration_sql()
    assert "CREATE VIEW student_point_summary" in sql
    assert "CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log" in sql
    assert "CREATE TYPE" not in sql                       # VARCHAR + CHECK, never native ENUM types
    view_sql = sql[sql.index("CREATE VIEW"):sql.index("GROUP BY")]
    for column in student_point_summary.columns:          # the Table used to query the view matches its columns
        assert re.search(rf"\b{column.name}\b", view_sql), column.name


def test_circular_foreign_key_is_added_after_both_tables():
    sql = migration_sql()
    fk = sql.index("ALTER TABLE point_request ADD CONSTRAINT fk_point_request_correction_of_ledger_id_point_ledger")
    assert sql.index("CREATE TABLE point_request ") < sql.index("CREATE TABLE point_ledger ") < fk
