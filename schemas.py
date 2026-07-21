from typing import Optional

from pydantic import BaseModel, Field

from models import PRIORITIES, STATUSES, ROLES


class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    password: str
    name: str


class UserCreateRequest(BaseModel):
    username: str
    password: str
    name: str
    role: str = Field(default="engineer")
    color: Optional[str] = None

    def validate_role(self):
        if self.role not in ROLES:
            raise ValueError(f"role must be one of {ROLES}")


class ProjectCreateRequest(BaseModel):
    name: str
    color: str = "#0f6e5c"


class TaskCreateRequest(BaseModel):
    projectId: int
    title: str
    description: str = ""
    assigneeId: Optional[int] = None
    priority: str = "medium"
    dueDate: Optional[int] = None

    def validate_priority(self):
        if self.priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")


class TaskUpdateRequest(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    projectId: Optional[int] = None
    assigneeId: Optional[int] = None
    priority: Optional[str] = None
    dueDate: Optional[int] = None
    status: Optional[str] = None

    def validate_fields(self):
        if self.priority is not None and self.priority not in PRIORITIES:
            raise ValueError(f"priority must be one of {PRIORITIES}")
        if self.status is not None and self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
