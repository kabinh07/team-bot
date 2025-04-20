import logging
import random
import datetime
import dateparser
from collections import defaultdict
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    ContextTypes, filters
)
from sqlalchemy import create_engine, Column, Integer, String, DateTime
from sqlalchemy.orm import sessionmaker, declarative_base
import os
import openai

# --- Configuration ---
TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "YOUR_OPENAI_KEY")
openai.api_key = OPENAI_API_KEY
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///tasks.db")

# --- Database Setup ---
Base = declarative_base()

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    chat_id = Column(String)
    description = Column(String)
    status = Column(String)
    timestamp = Column(DateTime)

engine = create_engine(DATABASE_URL)
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine)
session = Session()

joined_chats = set()
scheduler = BackgroundScheduler()
scheduler.start()

# --- Logging ---
logging.basicConfig(level=logging.INFO)

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

# --- Bot Commands ---
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    joined_chats.add(update.effective_chat.id)
    await update.message.reply_text("Hi! I'm your AI task bot. Use /addtask, /listtasks, /schedule and more!")

async def add_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    task_desc = " ".join(context.args)
    task = Task(chat_id=chat_id, description=task_desc, status="pending", timestamp=datetime.datetime.now())
    session.add(task)
    session.commit()
    await update.message.reply_text(f"✅ Task added: {task_desc}")

async def list_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    task_list = session.query(Task).filter_by(chat_id=chat_id).all()
    if not task_list:
        await update.message.reply_text("📭 No tasks yet.")
        return
    text = "\n".join([f"{i+1}. {t.description} - {t.status}" for i, t in enumerate(task_list)])
    await update.message.reply_text(f"🗂️ Tasks:\n{text}")

async def mark_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    try:
        task_id = int(context.args[0]) - 1
        task_list = session.query(Task).filter_by(chat_id=chat_id).all()
        task = task_list[task_id]
        task.status = 'done'
        session.commit()
        await update.message.reply_text("✅ Task marked as done!")
    except:
        await update.message.reply_text("❗ Please provide a valid task number.")

async def schedule_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        time_str, msg = context.args[0], " ".join(context.args[1:])
        run_time = datetime.datetime.strptime(time_str, "%H:%M")
        today = datetime.date.today()
        send_time = datetime.datetime.combine(today, run_time)
        scheduler.add_job(
            lambda: context.bot.send_message(chat_id=update.effective_chat.id, text=msg),
            trigger='date', run_date=send_time
        )
        await update.message.reply_text(f"⏰ Message scheduled at {time_str}")
    except Exception as e:
        await update.message.reply_text(f"❗ Error: {e}")

async def smart_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    message = update.message.text
    time = dateparser.parse(message)
    task_text = message.replace("Remind me to", "").strip()
    if time:
        task = Task(chat_id=chat_id, description=task_text, status="pending", timestamp=datetime.datetime.now())
        session.add(task)
        session.commit()
        scheduler.add_job(
            lambda: context.bot.send_message(chat_id=chat_id, text=f"🔔 Reminder: {task_text}"),
            trigger='date', run_date=time
        )
        await update.message.reply_text(f"📝 Task and reminder added for {time.strftime('%Y-%m-%d %H:%M')}")
    else:
        await update.message.reply_text("❗ Could not parse time. Try again.")

# --- Bonus Features ---
async def gpt_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    prompt = " ".join(context.args)
    gpt_response = ask_gpt(f"Create a task from this prompt: {prompt}")
    await update.message.reply_text(f"🧠 GPT suggests: {gpt_response}")
    task = Task(chat_id=str(update.effective_chat.id), description=gpt_response, status="pending", timestamp=datetime.datetime.now())
    session.add(task)
    session.commit()

async def gpt_motivate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    response = ask_gpt("Give a motivational quote for someone managing tasks.")
    await update.message.reply_text(f"💬 GPT Motivation:\n{response}")

async def gpt_report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = str(update.effective_chat.id)
    task_list = session.query(Task).filter_by(chat_id=chat_id).all()
    summary = "\n".join([f"- {t.description} ({t.status})" for t in task_list])
    gpt_response = ask_gpt(f"Analyze this task list and give a short productivity report:\n{summary}")
    await update.message.reply_text(f"📊 GPT Productivity Report:\n{gpt_response}")

# --- Main ---
app = ApplicationBuilder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))
app.add_handler(CommandHandler("addtask", add_task))
app.add_handler(CommandHandler("listtasks", list_tasks))
app.add_handler(CommandHandler("done", mark_done))
app.add_handler(CommandHandler("schedule", schedule_msg))
app.add_handler(CommandHandler("gptask", gpt_task))
app.add_handler(CommandHandler("motivate", gpt_motivate))
app.add_handler(CommandHandler("report", gpt_report))
app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, smart_task))

if __name__ == '__main__':
    print("Bot is running...")
    app.run_polling()
