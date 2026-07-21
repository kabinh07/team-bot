"""Task-timer and dashboard-aggregation logic shared by the REST API and the
Telegram bot. Ported verbatim (same semantics) from the web/kanban mock:
elapsed time accrues while a task is in todo/inprogress, is banked into
accumulated_ms when it stops (review/done), and resumes when a task is
dragged back into todo/inprogress.
"""
from collections import defaultdict

from sqlalchemy import func

from models import (
    Session, User, Project, Task, STATUSES, STATUS_DONE, STATUS_META, now_ms,
)

RUNNING_STATUSES = {"todo", "inprogress"}


def is_running(status: str) -> bool:
    return status in RUNNING_STATUSES


def elapsed_ms(task: Task, now: int = None) -> int:
    now = now if now is not None else now_ms()
    running_extra = (now - task.last_resume_at_ms) if is_running(task.status) and task.last_resume_at_ms else 0
    return task.accumulated_ms + running_extra


def format_duration(ms: int) -> str:
    total_min = max(0, round(ms / 60000))
    h, m = divmod(total_min, 60)
    if h <= 0:
        return f"{m}m"
    if h < 24:
        return f"{h}h {m}m"
    d, h = divmod(h, 24)
    return f"{d}d {h}h"


def apply_status_change(task: Task, new_status: str, now: int = None) -> None:
    """Mutates task.status/accumulated_ms/last_resume_at_ms in place.
    Caller is responsible for committing the session.
    """
    now = now if now is not None else now_ms()
    if new_status not in STATUSES:
        raise ValueError(f"invalid status: {new_status}")
    was_running = is_running(task.status)
    will_run = is_running(new_status)
    if was_running and not will_run:
        if task.last_resume_at_ms:
            task.accumulated_ms += now - task.last_resume_at_ms
        task.last_resume_at_ms = None
    elif not was_running and will_run:
        task.last_resume_at_ms = now
    task.status = new_status


def task_to_public(task: Task, now: int = None) -> dict:
    now = now if now is not None else now_ms()
    return {
        "id": task.id,
        "projectId": task.project_id,
        "assigneeId": task.assignee_id,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "dueDate": task.due_date_ms,
        "status": task.status,
        "createdAt": task.created_at_ms,
        "elapsedMs": elapsed_ms(task, now),
        "elapsedLabel": format_duration(elapsed_ms(task, now)),
    }


# ---- dashboard aggregation ----

def summary_tiles(session) -> list:
    tasks = session.query(Task).all()
    projects = session.query(Project).count()
    now = now_ms()
    total_hours = sum(elapsed_ms(t, now) for t in tasks) / 3600000
    engineers = len({t.assignee_id for t in tasks})
    return [
        {"label": "Total tasks", "value": str(len(tasks))},
        {"label": "Hours logged", "value": f"{total_hours:.1f}h"},
        {"label": "Projects", "value": str(projects)},
        {"label": "Engineers", "value": str(engineers)},
    ]


def leaderboard(session, project_id: int = None) -> list:
    now = now_ms()
    q = session.query(Task)
    if project_id is not None:
        q = q.filter(Task.project_id == project_id)
    tasks = q.all()
    by_user = defaultdict(int)
    for t in tasks:
        by_user[t.assignee_id] += elapsed_ms(t, now)
    users = {u.id: u for u in session.query(User).all()}
    rows = []
    for uid, ms in by_user.items():
        u = users.get(uid)
        if not u:
            continue
        rows.append({"id": u.id, "name": u.name, "initials": u.initials,
                      "color": u.color, "hours": ms / 3600000})
    rows.sort(key=lambda r: r["hours"], reverse=True)
    max_hours = max([r["hours"] for r in rows] + [0.001])
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        r["hoursLabel"] = f"{r['hours']:.1f}h"
        r["widthPct"] = max(3, round(r["hours"] / max_hours * 100))
    return rows


def project_stats(session) -> list:
    now = now_ms()
    projects = session.query(Project).all()
    stats = []
    for p in projects:
        ptasks = session.query(Task).filter(Task.project_id == p.id).all()
        by_status = []
        for sm in STATUS_META:
            count = sum(1 for t in ptasks if t.status == sm["key"])
            pct = (count / len(ptasks) * 100) if ptasks else 0
            by_status.append({"key": sm["key"], "label": sm["label"], "dot": sm["dot"], "pct": pct})
        done_tasks = [t for t in ptasks if t.status == STATUS_DONE]
        avg_completion = (
            sum(elapsed_ms(t, now) for t in done_tasks) / len(done_tasks) / 3600000
            if done_tasks else 0
        )
        total_hours = sum(elapsed_ms(t, now) for t in ptasks) / 3600000
        contrib_raw = defaultdict(int)
        for t in ptasks:
            contrib_raw[t.assignee_id] += elapsed_ms(t, now)
        users = {u.id: u for u in session.query(User).all()}
        contributors = []
        for uid, ms in contrib_raw.items():
            if ms <= 0:
                continue
            u = users.get(uid)
            if not u:
                continue
            contributors.append({"id": u.id, "name": u.name, "initials": u.initials,
                                  "color": u.color, "hours": ms / 3600000})
        contributors.sort(key=lambda c: c["hours"], reverse=True)
        max_contrib = max([c["hours"] for c in contributors] + [0.001])
        for c in contributors:
            c["hoursLabel"] = f"{c['hours']:.1f}h"
            c["widthPct"] = max(4, round(c["hours"] / max_contrib * 100))
        stats.append({
            "id": p.id, "name": p.name, "color": p.color,
            "totalTasks": len(ptasks), "byStatus": by_status,
            "avgCompletionLabel": f"{avg_completion:.1f}h" if done_tasks else "—",
            "engagedUsers": len({t.assignee_id for t in ptasks}),
            "totalHoursLabel": f"{total_hours:.1f}h",
            "contributors": contributors,
        })
    return stats


def user_detail(session, user_id: int) -> dict | None:
    now = now_ms()
    u = session.query(User).get(user_id)
    if not u:
        return None
    utasks = session.query(Task).filter(Task.assignee_id == user_id).all()
    total_hours = sum(elapsed_ms(t, now) for t in utasks) / 3600000
    projects = session.query(Project).all()
    per_project = []
    for p in projects:
        ptasks = [t for t in utasks if t.project_id == p.id]
        if not ptasks:
            continue
        hours = sum(elapsed_ms(t, now) for t in ptasks) / 3600000
        by_status = [
            {"label": sm["label"], "dot": sm["dot"], "count": sum(1 for t in ptasks if t.status == sm["key"])}
            for sm in STATUS_META
        ]
        per_project.append({
            "id": p.id, "name": p.name, "color": p.color,
            "hoursLabel": f"{hours:.1f}h" if hours > 0 else "—",
            "byStatus": by_status,
        })
    return {
        "id": u.id, "name": u.name, "initials": u.initials, "color": u.color,
        "totalHoursLabel": f"{total_hours:.1f}h", "totalTasks": len(utasks),
        "doneTasks": sum(1 for t in utasks if t.status == STATUS_DONE),
        "perProject": per_project,
    }
