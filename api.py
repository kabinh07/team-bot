import logging
import os
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

import services
from auth import (
    create_token, current_user, get_db, hash_password, require_admin,
    require_project_creator, scope_department, verify_password,
)
from models import (
    DEFAULT_COLORS, Department, LinkCode, Project, ROLE_ADMIN, ROLE_ENGINEER,
    Task, User, _initials, now_ms,
)
from schemas import (
    DepartmentCreateRequest, LoginRequest, ProjectCreateRequest,
    ProjectUpdateRequest, SignupRequest, TaskCreateRequest,
    TaskUpdateRequest, UserCreateRequest, UserUpdateRequest,
)

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="Kanvan API")


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
    if not db.query(Department).get(body.departmentId):
        raise HTTPException(status_code=404, detail="Department not found")
    idx = db.query(User).count() % len(DEFAULT_COLORS)
    u = User(
        username=username, password_hash=hash_password(body.password),
        name=name, initials=_initials(name), color=DEFAULT_COLORS[idx],
        role=ROLE_ENGINEER, department_id=body.departmentId,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return {"token": create_token(u), "user": u.to_public()}


@app.get("/api/auth/me")
def me(user: User = Depends(current_user)):
    return user.to_public()


# ---- departments ----

@app.get("/api/departments")
def list_departments(db=Depends(get_db)):
    return [d.to_public() for d in db.query(Department).order_by(Department.name).all()]


@app.post("/api/departments")
def create_department(body: DepartmentCreateRequest, admin: User = Depends(require_admin), db=Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Department name is required")
    if db.query(Department).filter(Department.name == body.name.strip()).first():
        raise HTTPException(status_code=409, detail="Department already exists")
    d = Department(name=body.name.strip(), color=body.color)
    db.add(d)
    db.commit()
    db.refresh(d)
    return d.to_public()


# ---- users ----

@app.get("/api/users")
def list_users(department: int | None = None, user: User = Depends(current_user), db=Depends(get_db)):
    dept = scope_department(user, department)
    q = db.query(User)
    if dept is not None:
        q = q.filter(User.department_id == dept)
    return [u.to_public() for u in q.order_by(User.name).all()]


@app.post("/api/users")
def create_user(body: UserCreateRequest, admin: User = Depends(require_admin), db=Depends(get_db)):
    try:
        body.validate_role()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    if db.query(User).filter(User.username == body.username).first():
        raise HTTPException(status_code=409, detail="Username already taken")
    if body.departmentId is not None and not db.query(Department).get(body.departmentId):
        raise HTTPException(status_code=404, detail="Department not found")
    idx = db.query(User).count() % len(DEFAULT_COLORS)
    u = User(
        username=body.username,
        password_hash=hash_password(body.password),
        name=body.name,
        initials=_initials(body.name),
        color=body.color or DEFAULT_COLORS[idx],
        role=body.role,
        department_id=body.departmentId,
        can_create_projects=int(body.canCreateProjects),
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u.to_public()


@app.patch("/api/users/{user_id}")
def update_user(user_id: int, body: UserUpdateRequest, admin: User = Depends(require_admin), db=Depends(get_db)):
    try:
        body.validate_fields()
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    u = db.query(User).get(user_id)
    if not u:
        raise HTTPException(status_code=404, detail="User not found")
    if body.departmentId is not None:
        if not db.query(Department).get(body.departmentId):
            raise HTTPException(status_code=404, detail="Department not found")
        u.department_id = body.departmentId
    if body.canCreateProjects is not None:
        u.can_create_projects = int(body.canCreateProjects)
    if body.role is not None:
        u.role = body.role
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
def get_user_detail(user_id: int, department: int | None = None,
                     admin: User = Depends(require_admin), db=Depends(get_db)):
    detail = services.user_detail(db, user_id, department)
    if not detail:
        raise HTTPException(status_code=404, detail="User not found")
    return detail


# ---- projects ----

@app.get("/api/projects")
def list_projects(department: int | None = None, user: User = Depends(current_user), db=Depends(get_db)):
    dept = scope_department(user, department)
    q = db.query(Project)
    if dept is not None:
        q = q.filter(Project.department_id == dept)
    return [p.to_public() for p in q.order_by(Project.id).all()]


@app.post("/api/projects")
def create_project(body: ProjectCreateRequest, creator: User = Depends(require_project_creator), db=Depends(get_db)):
    if not body.name.strip():
        raise HTTPException(status_code=422, detail="Project name is required")
    if creator.role == ROLE_ADMIN:
        if not body.departmentId:
            raise HTTPException(status_code=422, detail="departmentId is required")
        dept_id = body.departmentId
    else:
        dept_id = creator.department_id
    if not db.query(Department).get(dept_id):
        raise HTTPException(status_code=404, detail="Department not found")
    p = Project(name=body.name.strip(), color=body.color, department_id=dept_id, created_by=creator.id)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p.to_public()


@app.patch("/api/projects/{project_id}")
def update_project(project_id: int, body: ProjectUpdateRequest, admin: User = Depends(require_admin), db=Depends(get_db)):
    p = db.query(Project).get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    if body.name is not None:
        if not body.name.strip():
            raise HTTPException(status_code=422, detail="Project name cannot be empty")
        p.name = body.name.strip()
    if body.color is not None:
        p.color = body.color
    db.commit()
    db.refresh(p)
    return p.to_public()


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: int, admin: User = Depends(require_admin), db=Depends(get_db)):
    p = db.query(Project).get(project_id)
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    db.query(Task).filter(Task.project_id == project_id).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


# ---- tasks ----

@app.get("/api/tasks")
def list_tasks(project: int | None = None, assignee: int | None = None, department: int | None = None,
                user: User = Depends(current_user), db=Depends(get_db)):
    dept = scope_department(user, department)
    q = db.query(Task)
    if dept is not None:
        q = q.join(Project, Task.project_id == Project.id).filter(Project.department_id == dept)
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
    project = db.query(Project).get(body.projectId)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if user.role != ROLE_ADMIN and project.department_id != user.department_id:
        raise HTTPException(status_code=403, detail="Cannot create tasks in another department's project")
    assignee_id = body.assigneeId or user.id
    assignee = db.query(User).get(assignee_id)
    if not assignee:
        raise HTTPException(status_code=404, detail="Assignee not found")
    if user.role != ROLE_ADMIN and assignee.department_id != user.department_id:
        raise HTTPException(status_code=403, detail="Cannot assign tasks to another department's user")
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
    if user.role != ROLE_ADMIN and t.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="You can only modify your own tasks")

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
    if user.role != ROLE_ADMIN and t.assignee_id != user.id:
        raise HTTPException(status_code=403, detail="You can only modify your own tasks")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ---- dashboard ----

@app.get("/api/dashboard")
def dashboard(department: int | None = None, user: User = Depends(current_user), db=Depends(get_db)):
    dept = scope_department(user, department)
    return {
        "summaryTiles": services.summary_tiles(db, dept),
        "leaderboard": services.leaderboard(db, department_id=dept),
        "projectStats": services.project_stats(db, dept),
    }


@app.get("/api/dashboard/report.csv")
def engagement_report(month: str | None = None, department: int | None = None,
                       admin: User = Depends(require_admin), db=Depends(get_db)):
    if month:
        try:
            year_s, month_s = month.split("-")
            year, mon = int(year_s), int(month_s)
            if not (1 <= mon <= 12):
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=422, detail="month must be in YYYY-MM format")
    else:
        today = datetime.now(timezone.utc)
        year, mon = today.year, today.month
    csv_text = services.engagement_report_csv(db, year, mon, department)
    filename = f"kanvan-engagement-{year:04d}-{mon:02d}.csv"
    return Response(
        content=csv_text, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---- portal static files (mounted last so /api/* takes precedence) ----
_web_dir = os.path.join(os.path.dirname(__file__), "web", "kanvan")
if os.path.isdir(_web_dir):
    app.mount("/", StaticFiles(directory=_web_dir, html=True), name="portal")
