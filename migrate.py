# migrate.py
import glob
import os
import shutil

from sqlalchemy import text

from models import engine

FALLBACK_DEPARTMENT = "AI/ML"


def reset_alembic_bookkeeping():
    """alembic/versions is gitignored and regenerated fresh every boot, but
    it lives on whatever machine is running the container and isn't synced
    with the DB's alembic_version stamp or with any other machine's copy.
    A crash mid-migration (e.g. under `restart: always`) leaves a stale
    revision file behind; the next boot's autogenerate builds on top of it
    and can replay an already-applied operation against a DB that's since
    moved on, crashing with things like DuplicateColumn. Since this project
    never relies on real migration history (each boot autogenerates a fresh
    diff against current models), just wipe local script files and the DB's
    stamp every boot so autogenerate always starts from a clean, accurate
    diff of live schema vs. models -- self-healing regardless of past
    crashes or which machine ran them.
    """
    for f in glob.glob("alembic/versions/*.py"):
        os.remove(f)
    shutil.rmtree("alembic/versions/__pycache__", ignore_errors=True)
    with engine.begin() as conn:
        if _table_exists(conn, "alembic_version"):
            conn.execute(text("DELETE FROM alembic_version"))


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


reset_alembic_bookkeeping()
preflight_department_backfill()
os.system("alembic revision --autogenerate -m 'autogen'")
os.system("alembic upgrade head")
backfill_user_departments()
