import logging
import os
from datetime import datetime, timezone

import dateparser
import openai
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)

import services
from auth import hash_password
from models import (
    BDT, LinkCode, Project, ROLE_ADMIN, ROLE_ENGINEER, STATUSES, Session,
    Task, User, _initials, now_ms,
)

# --- Configuration ---
TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_KEY")
openai.api_key = OPENAI_API_KEY

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("bot")

scheduler = BackgroundScheduler(timezone=BDT)
scheduler.start()

daily_motivation_activation = False


# --- OpenAI Assistant ---
def ask_gpt(prompt):
    try:
        response = openai.ChatCompletion.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful assistant who creates tasks and gives motivational advice."},
                {"role": "user", "content": prompt}
            ]
        )
        return response.choices[0].message['content'].strip()
    except Exception as e:
        logging.error(f"OpenAI error: {e}")
        return "There was a problem contacting ChatGPT."


# --- Identity helpers ---
def resolve_user(db, update: Update):
    tg_id = str(update.effective_user.id)
    return db.query(User).filter(User.telegram_id == tg_id).first()


def default_project(db):
    return db.query(Project).order_by(Project.id).first()


LINK_HINT = "🔗 Your Telegram isn't linked to a Kanbann account yet.\nGet a code from the portal (Settings) and send /link <code>."


async def require_linked_user(update: Update, db):
    user = resolve_user(db, update)
    if not user:
        await update.message.reply_text(LINK_HINT)
        return None
    return user


async def require_admin_user(update: Update, db):
    user = await require_linked_user(update, db)
    if user and user.role != ROLE_ADMIN:
        await update.message.reply_text("⛔ Admin access required.")
        return None
    return user


# --- Common commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        user = resolve_user(db, update)
        if user:
            await update.message.reply_text(
                f"👋 Welcome back, {user.name}! ({user.role})\n"
                "Try /projects, /mytasks, or /addtask <projectId> <title>."
            )
        else:
            await update.message.reply_text(
                "👋 Hi! I'm the Kanbann bot.\n" + LINK_HINT
            )
    finally:
        db.close()


async def link_account(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❗ Usage: /link <code>")
        return
    code = context.args[0].strip().upper()
    db = Session()
    try:
        lc = db.query(LinkCode).filter(LinkCode.code == code, LinkCode.used == 0).first()
        if not lc or lc.expires_at.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None):
            await update.message.reply_text("❗ That code is invalid or expired. Generate a new one from the portal.")
            return
        target = db.query(User).get(lc.user_id)
        target.telegram_id = str(update.effective_user.id)
        lc.used = 1
        db.commit()
        await update.message.reply_text(f"✅ Linked! You're now signed in as {target.name} ({target.role}).")
    finally:
        db.close()


# --- Engineer commands (also usable by admins) ---
async def list_projects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        user = await require_linked_user(update, db)
        if not user:
            return
        projects = db.query(Project).order_by(Project.id).all()
        if not projects:
            await update.message.reply_text("📭 No projects yet.")
            return
        text = "\n".join([f"{p.id}. {p.name}" for p in projects])
        await update.message.reply_text(f"📁 Projects:\n{text}")
    finally:
        db.close()


async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        user = await require_linked_user(update, db)
        if not user:
            return
        if len(context.args) < 2:
            await update.message.reply_text("❗ Usage: /addtask <projectId> <title>")
            return
        try:
            project_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❗ projectId must be a number. See /projects.")
            return
        project = db.query(Project).get(project_id)
        if not project:
            await update.message.reply_text("❗ Unknown project. See /projects.")
            return
        title = " ".join(context.args[1:]).strip()
        now = now_ms()
        task = Task(
            project_id=project.id, assignee_id=user.id, title=title, description="",
            priority="medium", due_date_ms=now + 3 * 86400000, status="todo",
            created_at_ms=now, accumulated_ms=0, last_resume_at_ms=now,
        )
        db.add(task)
        db.commit()
        await update.message.reply_text(f"✅ Task #{task.id} added to {project.name}: {title}")
    finally:
        db.close()


async def my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        user = await require_linked_user(update, db)
        if not user:
            return
        tasks = db.query(Task).filter(Task.assignee_id == user.id).order_by(Task.id).all()
        if not tasks:
            await update.message.reply_text("📭 You have no tasks.")
            return
        now = now_ms()
        lines = []
        for t in tasks:
            elapsed = services.format_duration(services.elapsed_ms(t, now))
            lines.append(f"{t.id}. [{t.status}] {t.title} · {elapsed}")
        await update.message.reply_text("🗂️ Your tasks:\n" + "\n".join(lines))
    finally:
        db.close()


async def move_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        user = await require_linked_user(update, db)
        if not user:
            return
        if len(context.args) < 2:
            await update.message.reply_text(f"❗ Usage: /move <taskId> <status>\nStatuses: {', '.join(STATUSES)}")
            return
        try:
            task_id = int(context.args[0])
        except ValueError:
            await update.message.reply_text("❗ taskId must be a number.")
            return
        new_status = context.args[1].strip().lower()
        if new_status not in STATUSES:
            await update.message.reply_text(f"❗ Status must be one of: {', '.join(STATUSES)}")
            return
        task = db.query(Task).get(task_id)
        if not task:
            await update.message.reply_text("❗ Task not found.")
            return
        if task.assignee_id != user.id and user.role != ROLE_ADMIN:
            await update.message.reply_text("⛔ You can only move your own tasks.")
            return
        services.apply_status_change(task, new_status)
        db.commit()
        elapsed = services.format_duration(services.elapsed_ms(task))
        await update.message.reply_text(f"✅ Task #{task.id} → {new_status} · {elapsed} logged")
    finally:
        db.close()


async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # shortcut: /done <taskId> == /move <taskId> done
    context.args = [context.args[0], "done"] if context.args else []
    await move_task(update, context)


# --- Admin commands ---
async def new_project(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        admin = await require_admin_user(update, db)
        if not admin:
            return
        if not context.args:
            await update.message.reply_text("❗ Usage: /newproject <name>")
            return
        name = " ".join(context.args).strip()
        project = Project(name=name, color="#0f6e5c", created_by=admin.id)
        db.add(project)
        db.commit()
        await update.message.reply_text(f"✅ Project #{project.id} created: {name}")
    finally:
        db.close()


async def list_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        admin = await require_admin_user(update, db)
        if not admin:
            return
        users = db.query(User).order_by(User.name).all()
        text = "\n".join([f"{u.id}. {u.name} (@{u.username}) · {u.role}{' · linked' if u.telegram_id else ''}" for u in users])
        await update.message.reply_text(f"👥 Users:\n{text}")
    finally:
        db.close()


async def add_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        admin = await require_admin_user(update, db)
        if not admin:
            return
        if len(context.args) < 2:
            await update.message.reply_text("❗ Usage: /adduser <username> <full name> [admin|engineer]")
            return
        username = context.args[0].strip()
        role = ROLE_ENGINEER
        rest = context.args[1:]
        if rest and rest[-1].lower() in (ROLE_ADMIN, ROLE_ENGINEER):
            role = rest[-1].lower()
            rest = rest[:-1]
        name = " ".join(rest).strip()
        if not name:
            await update.message.reply_text("❗ Usage: /adduser <username> <full name> [admin|engineer]")
            return
        if db.query(User).filter(User.username == username).first():
            await update.message.reply_text("❗ That username is taken.")
            return
        temp_password = os.urandom(4).hex()
        u = User(
            username=username, password_hash=hash_password(temp_password),
            name=name, initials=_initials(name), color="#0f6e5c", role=role,
        )
        db.add(u)
        db.commit()
        await update.message.reply_text(
            f"✅ Created {role} {name} (@{username})\n🔑 Temp password: {temp_password}\n"
            "Share this so they can log into the portal and generate a /link code."
        )
    finally:
        db.close()


async def dashboard_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        admin = await require_admin_user(update, db)
        if not admin:
            return
        tiles = services.summary_tiles(db)
        lb = services.leaderboard(db)[:5]
        tile_text = " · ".join(f"{t['value']} {t['label']}" for t in tiles)
        lb_text = "\n".join(f"{u['rank']}. {u['name']} — {u['hoursLabel']}" for u in lb)
        await update.message.reply_text(f"📊 {tile_text}\n\n🏆 Leaderboard:\n{lb_text}")
    finally:
        db.close()


async def project_stats_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        admin = await require_admin_user(update, db)
        if not admin:
            return
        stats = services.project_stats(db)
        if not stats:
            await update.message.reply_text("📭 No projects yet.")
            return
        lines = [
            f"{p['name']}: {p['totalTasks']} tasks · {p['engagedUsers']} engineers · {p['totalHoursLabel']} total"
            for p in stats
        ]
        await update.message.reply_text("📈 Project stats:\n" + "\n".join(lines))
    finally:
        db.close()


# --- Bonus features (kept from original) ---
async def schedule_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time_str, msg = context.args[0], " ".join(context.args[1:])
        run_time = datetime.strptime(time_str, "%H:%M")
        today = datetime.today().date()
        send_time = datetime.combine(today, run_time.time())
        scheduler.add_job(
            lambda: context.bot.send_message(chat_id=update.effective_chat.id, text=msg),
            trigger='date', run_date=send_time
        )
        await update.message.reply_text(f"⏰ Message scheduled at {time_str}")
    except Exception as e:
        await update.message.reply_text(f"❗ Error: {e}")


async def smart_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.message.text
    reminder_time = dateparser.parse(message)
    task_text = message.replace("Remind me to", "").strip()
    if not reminder_time:
        await update.message.reply_text("❗ Could not parse time. Try again, or use /addtask.")
        return
    chat_id = update.effective_chat.id
    db = Session()
    try:
        user = resolve_user(db, update)
        project = default_project(db) if user else None
        if user and project:
            now = now_ms()
            task = Task(
                project_id=project.id, assignee_id=user.id, title=task_text, description="",
                priority="medium", due_date_ms=int(reminder_time.timestamp() * 1000), status="todo",
                created_at_ms=now, accumulated_ms=0, last_resume_at_ms=now,
            )
            db.add(task)
            db.commit()
    finally:
        db.close()
    scheduler.add_job(
        lambda: context.bot.send_message(chat_id=chat_id, text=f"🔔 Reminder: {task_text}"),
        trigger='date', run_date=reminder_time
    )
    await update.message.reply_text(f"📝 Reminder set for {reminder_time.strftime('%Y-%m-%d %H:%M')}")


async def gpt_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    gpt_response = ask_gpt(f"Create a short, single-line task title from this prompt: {prompt}")
    await update.message.reply_text(f"🧠 GPT suggests: {gpt_response}")
    db = Session()
    try:
        user = resolve_user(db, update)
        project = default_project(db) if user else None
        if not user:
            await update.message.reply_text(LINK_HINT)
            return
        if not project:
            await update.message.reply_text("❗ No projects exist yet — ask an admin to /newproject first.")
            return
        now = now_ms()
        task = Task(
            project_id=project.id, assignee_id=user.id, title=gpt_response, description=prompt,
            priority="medium", due_date_ms=now + 3 * 86400000, status="todo",
            created_at_ms=now, accumulated_ms=0, last_resume_at_ms=now,
        )
        db.add(task)
        db.commit()
        await update.message.reply_text(f"✅ Added as task #{task.id} in {project.name}")
    finally:
        db.close()


async def gpt_motivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = ask_gpt("Give a motivational quote for someone managing tasks.")
    await update.message.reply_text(f"💬 GPT Motivation:\n{response}")


async def send_daily_motivation(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global daily_motivation_activation
    if daily_motivation_activation:
        await update.message.reply_text("💬 Daily Motivation already activated")
        return

    chat_id = update.effective_chat.id
    import asyncio
    loop = asyncio.get_running_loop()

    def send_motivation():
        try:
            motivation = ask_gpt("Give a motivational quote for someone managing tasks.")
            asyncio.run_coroutine_threadsafe(
                context.bot.send_message(chat_id=chat_id, text=f"💬 Daily Motivation:\n{motivation}"),
                loop
            )
        except Exception as e:
            logging.error(f"Failed to send motivation: {e}")

    scheduler.add_job(
        send_motivation, trigger='cron', hour=10, minute=0,
        timezone=BDT, misfire_grace_time=300
    )
    daily_motivation_activation = True
    await update.message.reply_text("📝 Daily Motivation scheduled at 10:00 AM every day.")


async def gpt_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = Session()
    try:
        user = resolve_user(db, update)
        if not user:
            await update.message.reply_text(LINK_HINT)
            return
        tasks = db.query(Task).filter(Task.assignee_id == user.id).all()
        summary = "\n".join([f"- {t.title} ({t.status})" for t in tasks]) or "(no tasks)"
    finally:
        db.close()
    gpt_response = ask_gpt(f"Analyze this task list and give a short productivity report:\n{summary}")
    await update.message.reply_text(f"📊 GPT Productivity Report:\n{gpt_response}")


# --- Main ---
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("link", link_account))
app.add_handler(CommandHandler("projects", list_projects))
app.add_handler(CommandHandler("addtask", add_task))
app.add_handler(CommandHandler("mytasks", my_tasks))
app.add_handler(CommandHandler("move", move_task))
app.add_handler(CommandHandler("done", mark_done))
app.add_handler(CommandHandler("newproject", new_project))
app.add_handler(CommandHandler("users", list_users))
app.add_handler(CommandHandler("adduser", add_user))
app.add_handler(CommandHandler("dashboard", dashboard_summary))
app.add_handler(CommandHandler("stats", project_stats_summary))
app.add_handler(CommandHandler("schedule", schedule_msg))
app.add_handler(CommandHandler("gptask", gpt_task))
app.add_handler(CommandHandler("motivate", gpt_motivate))
app.add_handler(CommandHandler("report", gpt_report))
app.add_handler(CommandHandler("active_daily_motivation", send_daily_motivation))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_task))

if __name__ == '__main__':
    # Migrations and seeding run once, in the `web` service. The bot only
    # starts after `web`'s healthcheck passes (see docker-compose.yaml),
    # so the schema is guaranteed to exist by the time we get here.
    logging.info("Bot is running...")
    app.run_polling()
