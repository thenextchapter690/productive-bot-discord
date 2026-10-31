
# Discord Productivity Bot

A modern Discord productivity bot built for study, coding, and tech communities.

Instead of remembering dozens of commands, members can access almost everything through a clean button-based menu.

## ✨ Features

- 🎯 Focus & Pomodoro sessions
- ✅ Daily goals and task manager
- 📊 XP, levels, streaks, and statistics
- 🏆 Leaderboards
- 🎁 Daily rewards
- 💻 Developer tools (code runner, GitHub, docs, AI tools)
- 📚 Flashcards with spaced repetition
- 🌿 Study rooms, reminders, polls & ambient sounds
- 🤖 One-click server setup

## 🚀 Quick Start

1. Create a Discord Application.
2. Invite the bot to your server.
3. Add your bot token to `.env`.
4. Install dependencies.

```bash
pip install -r requirements.txt
python bot.py
```

Run `/wizard` once and the bot will automatically set up the server for you.

## 🛠 Tech Stack

- Python 3.12+
- discord.py
- SQLite
- Pillow
- OpenAI-compatible APIs (optional)
- Railway (recommended deployment)

## 📁 Project Structure

```
bot.py
config.py
db.py
cardgen.py
cogs/
utils.py
```

## 📦 Deployment

The bot is ready for Railway deployment.

Simply connect your GitHub repository, add the required environment variables, attach a persistent volume, and deploy.

## 🤝 Contributing

Issues and pull requests are welcome.

## 📄 License

MIT License.
