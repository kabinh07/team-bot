import logging
import os

from fastapi import Depends, FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import services
from auth import (
    create_token, current_user, get_db, hash_password, require_admin,
    verify_password,
)
from models import (
    DEFAULT_COLORS, LinkCode, Project, ROLE_ADMIN, ROLE_ENGINEER, Task,
    User, _initials, now_ms,
)
from schemas import (
    LoginRequest, ProjectCreateRequest, SignupRequest, TaskCreateRequest,
    TaskUpdateRequest, UserCreateRequest,
)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Kanbann API")


# ---- auth ----

@app.post("/api/auth/login")
def login(body: LoginRequest, db=Depends(get_db)):
    user = db.query(User).filter(User.username == body.username).first()
    if not user or not verify_password(body.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"token": create_token(user), "user": user.to_public()}


@app.post("/api/auth/signup")
def signup(body: SignupRequest, db=Depends(get_db)):
    username = body.username.strip()
    name = body.name.strip()
    if not username or not body.password or not name:
        raise HTTPException(status_code=422, detail="Username, password and name are required")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(status_code=409, detail="Username already taken")
    idx = db.query(User).count() % len(DEFAULT_COLORS)
    u = User(
        username=username, password_hash=hash_password(body.password),
        name=name, initials=_initials(name), color=DEFAULT_COLORS[idx],
        role=ROLE_ENGINEER,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"token": create_token(u), "user": u.to_public()}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return user.to_public()


# ---- users ----

@app.get("/api/users")
def list_users(user: User = Depends(current_user), db=Depends(get_db)):
    return [u.to_public() for u in db.query(User).order_by(User.name).all()]


@app.post("/api/users")
def create_user(body: UserCreateRequest, admin: User = Depends(require_admin), db=Depends(get_db)):
    try:
        body.validate_role()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Username already taken")
    idx = db.query(User).count() % len(DEFAULT_COLORS)
    u = User(
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.name,
        initials=_initials(body.name),
        color=body.color or DEFAULT_COLORS[idx],
        role=body.role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u.to_public()


@app.post("/api/users/link-code")
def create_link_code(user: User = Depends(current_user), db=Depends(get_db)):
    lc = LinkCode.new_for(user.id)
    db.add(lc)
    db.commit()
    return {"code": lc.code, "expiresAt": lc.expires_at.isoformat()}


@app.get("/api/users/{user_id}/detail")
def get_user_detail(user_id: int, admin: User = Depends(require_admin), db=Depends(get_db)):
    detail = services.user_detail(db, user_id)
    if not detail:
        raise HTTPException(status_code=404, detail="User not found")
    return detail


# ---- projects ----

@app.get("/api/projects")
def list_projects(user: User = Depends(current_user), db=Depends(get_db)):
    return [p.to_public() for p in db.query(Project).order_by(Project.id).all()]


@app.post("/api/projects")
def create_project(body: ProjectCreateRequest, admin: User = Depends(require_admin), db=Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Project name is required")
    p = Project(name=body.name.strip(), color=body.color, created_by=admin.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p.to_public()


# ---- tasks ----

@app.get("/api/tasks")
def list_tasks(project: int | None = None, assignee: int | None = None,
                user: User = Depends(current_user), db=Depends(get_db)):
    q = db.query(Task)
    if project is not None:
        q = q.filter(Task.project_id == project)
    if assignee is not None:
        q = q.filter(Task.assignee_id == assignee)
    now = now_ms()
    return [services.task_to_public(t, now) for t in q.order_by(Task.id).all()]


@app.post("/api/tasks")
def create_task(body: TaskCreateRequest, user: User = Depends(current_user), db=Depends(get_db)):
    try:
        body.validate_priority()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if not body.title.strip():
        raise HTTPException(status_code=422, detail="Title is required")
    if not db.query(Project).get(body.projectId):
        raise HTTPException(status_code=404, detail="Project not found")
    assignee_id = body.assigneeId or user.id
    if not db.query(User).get(assignee_id):
        raise HTTPException(status_code=404, detail="Assignee not found")
    now = now_ms()
    t = Task(
        project_id=body.projectId, assignee_id=assignee_id,
        title=body.title.strip(), description=body.description.strip(),
        priority=body.priority, due_date_ms=body.dueDate or (now + 3 * 86400000),
        status="todo", created_at_ms=now, accumulated_ms=0, last_resume_at_ms=now,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return services.task_to_public(t)


@app.patch("/api/tasks/{task_id}")
def update_task(task_id: int, body: TaskUpdateRequest, user: User = Depends(current_user), db=Depends(get_db)):
    try:
        body.validate_fields()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    t = db.query(Task).get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.title is not None:
        if not body.title.strip():
            raise HTTPException(status_code=422, detail="Title cannot be empty")
        t.title = body.title.strip()
    if body.description is not None:
        t.description = body.description.strip()
    if body.projectId is not None:
        if not db.query(Project).get(body.projectId):
            raise HTTPException(status_code=404, detail="Project not found")
        t.project_id = body.projectId
    if body.assigneeId is not None:
        if not db.query(User).get(body.assigneeId):
            raise HTTPException(status_code=404, detail="Assignee not found")
        t.assignee_id = body.assigneeId
    if body.priority is not None:
        t.priority = body.priority
    if body.dueDate is not None:
        t.due_date_ms = body.dueDate
    if body.status is not None and body.status != t.status:
        services.apply_status_change(t, body.status)

    db.commit()
    db.refresh(t)
    return services.task_to_public(t)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, user: User = Depends(current_user), db=Depends(get_db)):
    t = db.query(Task).get(task_id)
    if not t:
        raise HTTPException(status_code=404, detail="Task not found")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---- dashboard ----

@app.get("/api/dashboard")
def dashboard(user: User = Depends(current_user), db=Depends(get_db)):
    return {
        "summaryTiles": services.summary_tiles(db),
        "leaderboard": services.leaderboard(db),
        "projectStats": services.project_stats(db),
    }


# ---- portal static files (mounted last so /api/* takes precedence) ----
_web_dir = os.path.join(os.path.dirname(__file__), "web", "kanban")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="portal")
