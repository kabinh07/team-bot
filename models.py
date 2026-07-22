import os
import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    create_engine, Column, Integer, String, DateTime, BigInteger,
    ForeignKey, Enum as SAEnum, Text
)
from sqlalchemy.orm import sessionmaker, declarative_base, relationship

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///tasks.db")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
Session = sessionmaker(bind=engine)
Base = declarative_base()

BDT = timezone(timedelta(hours=6))

ROLE_ADMIN = "admin"
ROLE_ENGINEER = "engineer"
ROLES = (ROLE_ADMIN, ROLE_ENGINEER)

STATUS_TODO = "todo"
STATUS_INPROGRESS = "inprogress"
STATUS_REVIEW = "review"
STATUS_DONE = "done"
STATUSES = (STATUS_TODO, STATUS_INPROGRESS, STATUS_REVIEW, STATUS_DONE)

STATUS_META = [
    {"key": STATUS_TODO, "label": "To Do", "dot": "#64748b"},
    {"key": STATUS_INPROGRESS, "label": "In Progress", "dot": "#1d4ed8"},
    {"key": STATUS_REVIEW, "label": "Review", "dot": "#b45309"},
    {"key": STATUS_DONE, "label": "Done", "dot": "#0f6e5c"},
]

PRIORITIES = ("low", "medium", "high")

DEFAULT_COLORS = ['#0f6e5c', '#1d4ed8', '#b45309', '#a21caf', '#b91c1c', '#4d7c0f']


def now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _gen_code(n=8):
    return "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(n))


def _initials(name: str) -> str:
    parts = [p for p in name.strip().split() if p]
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


class Department(Base):
    __tablename__ = 'departments'
    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    color = Column(String, nullable=False, default='#0f6e5c')
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    def to_public(self):
        return {"id": self.id, "name": self.name, "color": self.color}


class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)
    username = Column(String, unique=True, nullable=False, index=True)
    password_hash = Column(String, nullable=False)
    name = Column(String, nullable=False)
    initials = Column(String(4), nullable=False)
    color = Column(String, nullable=False, default='#0f6e5c')
    role = Column(SAEnum(*ROLES, name='user_role'), nullable=False, default=ROLE_ENGINEER)
    telegram_id = Column(String, unique=True, nullable=True, index=True)
    department_id = Column(Integer, ForeignKey('departments.id'), nullable=True, index=True)
    can_create_projects = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    department = relationship('Department')

    def to_public(self):
        return {
            "id": self.id, "username": self.username, "name": self.name,
            "initials": self.initials, "color": self.color, "role": self.role,
            "telegramLinked": bool(self.telegram_id),
            "departmentId": self.department_id,
            "canCreateProjects": bool(self.can_create_projects),
        }


class Project(Base):
    __tablename__ = 'projects'
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    color = Column(String, nullable=False, default='#0f6e5c')
    department_id = Column(Integer, ForeignKey('departments.id'), nullable=False, index=True)
    created_by = Column(Integer, ForeignKey('users.id'), nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    department = relationship('Department')

    def to_public(self):
        return {"id": self.id, "name": self.name, "color": self.color, "departmentId": self.department_id}


class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, ForeignKey('projects.id'), nullable=False, index=True)
    assignee_id = Column(Integer, ForeignKey('users.id'), nullable=False, index=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False, default='')
    priority = Column(SAEnum(*PRIORITIES, name='task_priority'), nullable=False, default='medium')
    due_date_ms = Column(BigInteger, nullable=True)
    status = Column(SAEnum(*STATUSES, name='task_status'), nullable=False, default=STATUS_TODO)
    created_at_ms = Column(BigInteger, nullable=False)
    accumulated_ms = Column(BigInteger, nullable=False, default=0)
    last_resume_at_ms = Column(BigInteger, nullable=True)

    project = relationship('Project')
    assignee = relationship('User')


class LinkCode(Base):
    """One-time code used to bind a Telegram account to a portal User."""
    __tablename__ = 'link_codes'
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    code = Column(String, unique=True, nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False)
    used = Column(Integer, nullable=False, default=0)

    @staticmethod
    def new_for(user_id: int, ttl_minutes: int = 10) -> "LinkCode":
        return LinkCode(
            user_id=user_id,
            code=_gen_code(),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
        )


Base.metadata.create_all(engine)
