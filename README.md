# 🤖 Telegram AI Task Bot

A Telegram group bot that lets users manage tasks with natural language, schedule reminders, and stay motivated — now powered by ChatGPT for smart insights and task support.

---

## 🚀 Features

- ✅ Add and list tasks via `/addtask` and `/listtasks`
- ✨ Mark tasks as done with `/done <number>`
- 🧠 Natural language task creation (e.g. “Remind me to call John at 4 PM”)
- ⏰ Schedule group messages via `/schedule <HH:MM> <message>`
- 📋 Daily task summary at 9 AM
- 💬 Motivational messages at 8 AM
- 🤖 **NEW**: GPT-powered smart task creation with `/gptask <prompt>`
- 🌟 **NEW**: Daily motivation from ChatGPT with `/motivate`
- 📊 **NEW**: Group productivity analysis via `/report`
- 💾 SQLite database persistence
- 🐳 Fully containerized with Docker

---

## 🛠️ Setup Instructions

### 1. Clone the Repository
```bash
git clone https://github.com/yourusername/telegram-ai-task-bot.git
cd telegram-ai-task-bot
```

### 2. Set Your Bot Tokens

Update the `docker-compose.yml` environment section:

```yaml
environment:
  - TELEGRAM_TOKEN=your_telegram_token
  - OPENAI_API_KEY=your_openai_key
```

Or export them locally:

```bash
export TELEGRAM_TOKEN=your_telegram_token
export OPENAI_API_KEY=your_openai_key
```

### 3. Build & Run with Docker

```bash
docker-compose up --build
```

The bot will start and wait for messages in your Telegram group.

---

## 💬 Example Commands

```bash
/addtask Finish the report
/listtasks
/done 2
/schedule 18:30 Team call
/gptask Plan a small team-building exercise
/motivate
/report
```

Or simply type:

> Remind me to submit the tax form tomorrow at 3 PM

---

## 🧠 Powered By

- [OpenAI ChatGPT](https://platform.openai.com/)
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
- [SQLAlchemy](https://www.sqlalchemy.org/)
- [APScheduler](https://apscheduler.readthedocs.io/)
- [Docker](https://www.docker.com/)

---

## 🧪 To-Do & Ideas

- [ ] Web dashboard for task overview
- [ ] Group-specific preferences
- [ ] Optional voice command support
- [ ] Google Calendar integration

---

## 👨‍💻 Author

Built with ❤️ by [Your Name]

---

## 📜 License

MIT License