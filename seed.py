"""Idempotent startup seed: ensures an admin account exists, and optionally
seeds demo projects/users/tasks when SEED_DEMO=true. Safe to run every
container start.
"""
import logging
import os

from auth import hash_password
from models import (
    DEFAULT_COLORS, Project, ROLE_ADMIN, ROLE_ENGINEER, Session, Task, User,
    _initials, now_ms,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("seed")

DEMO_USERS = [
    ("maya", "Maya Chen"), ("jordan", "Jordan Lee"), ("priya", "Priya Nair"),
    ("sam", "Sam Torres"), ("ivy", "Ivy Zhao"), ("noah", "Noah Kim"),
]
DEMO_PROJECTS = [
    ("Atlas Redesign", "#0f6e5c"), ("Nimbus API", "#1d4ed8"), ("Fieldwork Mobile", "#b45309"),
]


def seed_admin(db):
    username = os.getenv("ADMIN_USERNAME", "admin")
    password = os.getenv("ADMIN_PASSWORD", "admin123")
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return existing
    admin = User(
        username=username, password_hash=hash_password(password),
        name="Admin", initials="AD", color="#1b1b18", role=ROLE_ADMIN,
    )
    db.add(admin)
    db.commit()
    log.info("Seeded admin user %r", username)
    return admin


def seed_demo(db):
    if db.query(Project).count() > 0:
        return
    users = []
    for i, (username, name) in enumerate(DEMO_USERS):
        u = User(
            username=username, password_hash=hash_password("password123"),
            name=name, initials=_initials(name),
            color=DEFAULT_COLORS[i % len(DEFAULT_COLORS)], role=ROLE_ENGINEER,
        )
        db.add(u)
        users.append(u)
    db.commit()

    projects = []
    for name, color in DEMO_PROJECTS:
        p = Project(name=name, color=color)
        db.add(p)
        projects.append(p)
    db.commit()

    now = now_ms()
    day = 86400000
    demo_tasks = [
        ("Design onboarding flow", "Redesign the first-run experience for new engineers.", "high", 3, "inprogress"),
        ("Fix navbar overflow bug", "Long project names break the top nav on small screens.", "medium", 1, "todo"),
        ("Write accessibility audit", "Full a11y pass across the redesigned screens.", "low", 5, "review"),
        ("Refactor theming tokens", "Consolidate color and spacing tokens into one source.", "medium", -1, "done"),
    ]
    for i, (title, desc, priority, due_days, status) in enumerate(demo_tasks):
        t = Task(
            project_id=projects[0].id, assignee_id=users[i % len(users)].id,
            title=title, description=desc, priority=priority,
            due_date_ms=now + due_days * day, status="todo",
            created_at_ms=now, accumulated_ms=0, last_resume_at_ms=now,
        )
        db.add(t)
        db.flush()
        if status != "todo":
            import services
            services.apply_status_change(t, status, now)
    db.commit()
    log.info("Seeded demo data: %d users, %d projects, %d tasks", len(users), len(projects), len(demo_tasks))


def run():
    db = Session()
    try:
        seed_admin(db)
        if os.getenv("SEED_DEMO", "false").lower() in ("1", "true", "yes"):
            seed_demo(db)
    finally:
        db.close()


if __name__ == "__main__":
    run()
