"""Make audit_log append-only (blocks UPDATE and DELETE)

Revision ID: 0002
Revises: 0001
"""
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        CREATE FUNCTION audit_log_immutable() RETURNS trigger AS $$
        BEGIN RAISE EXCEPTION 'audit_log is append-only'; END;
        $$ LANGUAGE plpgsql;
    """)
    op.execute("""
        CREATE TRIGGER audit_log_no_change BEFORE UPDATE OR DELETE ON audit_log
        FOR EACH ROW EXECUTE FUNCTION audit_log_immutable();
    """)


def downgrade():
    op.execute("DROP TRIGGER audit_log_no_change ON audit_log")
    op.execute("DROP FUNCTION audit_log_immutable()")
