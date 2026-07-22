# migrate.py
import os

from sqlalchemy import text

from models import engine

FALLBACK_DEPARTMENT = "AI/ML"


def _table_exists(conn, table):
    row = conn.execute(text(
        "SELECT 1 FROM information_schema.tables WHERE table_name = :t"
    ), {"t": table}).first()
    return row is not None


def _column_exists(conn, table, column):
    row = conn.execute(text(
        "SELECT 1 FROM information_schema.columns WHERE table_name = :t AND column_name = :c"
    ), {"t": table, "c": column}).first()
    return row is not None


def preflight_department_backfill():
    """Alembic autogenerate can't safely add a NOT NULL projects.department_id
    column to a table that already has rows -- there's no sane constant
    default for a foreign key, so the ALTER crashes mid-migration on any
    database with existing projects. Handle it ourselves before alembic ever
    sees it: ensure a fallback department exists, add the column nullable if
    missing, backfill any NULLs to it, then enforce NOT NULL. Idempotent and
    a no-op on a fresh database (alembic creates everything normally there).
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        if not _table_exists(conn, "departments"):
            conn.execute(text(
                "CREATE TABLE departments ("
                "id SERIAL PRIMARY KEY, "
                "name VARCHAR NOT NULL UNIQUE, "
                "color VARCHAR NOT NULL DEFAULT '#0f6e5c', "
                "created_at TIMESTAMP)"
            ))
        dept_id = conn.execute(
            text("SELECT id FROM departments WHERE name = :n"), {"n": FALLBACK_DEPARTMENT}
        ).scalar()
        if dept_id is None:
            dept_id = conn.execute(
                text("INSERT INTO departments (name, color) VALUES (:n, '#0f6e5c') RETURNING id"),
                {"n": FALLBACK_DEPARTMENT},
            ).scalar()

        if _table_exists(conn, "projects"):
            if not _column_exists(conn, "projects", "department_id"):
                conn.execute(text("ALTER TABLE projects ADD COLUMN department_id INTEGER"))
            conn.execute(
                text("UPDATE projects SET department_id = :d WHERE department_id IS NULL"),
                {"d": dept_id},
            )
            conn.execute(text("ALTER TABLE projects ALTER COLUMN department_id SET NOT NULL"))


def backfill_user_departments():
    """Existing engineer accounts get NULL department_id once alembic adds
    the column, which would silently lock them out (no department = no
    visibility). Assign them to the same fallback department post-migration.
    """
    if engine.dialect.name != "postgresql":
        return
    with engine.begin() as conn:
        if not _table_exists(conn, "users") or not _column_exists(conn, "users", "department_id"):
            return
        dept_id = conn.execute(
            text("SELECT id FROM departments WHERE name = :n"), {"n": FALLBACK_DEPARTMENT}
        ).scalar()
        if dept_id is None:
            return
        conn.execute(
            text("UPDATE users SET department_id = :d WHERE department_id IS NULL AND role = 'engineer'"),
            {"d": dept_id},
        )


preflight_department_backfill()
os.system("alembic revision --autogenerate -m 'autogen'")
os.system("alembic upgrade head")
backfill_user_departments()
