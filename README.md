# Discord Productivity Bot

One Python bot for a study / focus / tech community server. **Members never need to type a command**:
everything is reachable from a button menu (`/menu`, or the permanent panel in your `#hub` channel).
Slash commands exist too, for people who prefer them.

## For members: just tap buttons

Type `/menu` (or press **Open the menu** in the welcome message, or use the pinned panel). You get:

| Button | What it opens |
|---|---|
| 🎯 **Focus** | One-tap 25 / 50 / 90 minute sessions or a custom one. Others press **Join** to focus together. |
| ✅ **Goals & Tasks** | Set today's goal, mark it done, add tasks, tick them off from a dropdown, make your list public/private. |
| 📊 **My Stats** | A designed stats card: level and XP bar, today / 7 days / all time, streak, and a 26-week study heatmap. Share it to the channel. |
| 🏆 **Leaderboard** | Today / 7 days / month / all time / XP rankings. |
| 🎁 **Daily Bonus** | Free XP once a day, bigger with a claim streak. |
| 💻 **Tech Tools** | Run code, AI quiz, explain an error, daily challenge, tech news, trending repos, docs, learning paths, code snippets, GitHub and PyPI lookup, code-review requests. |
| 🌿 **Study Tools** | Reminders, polls, focus sounds, accountability partner, study-room info, private-room controls (lock, invite, rename, limit), quiet hours (mods). |
| 📚 **Flashcards** | Add cards, review what is due (spaced repetition), or take an AI quiz. |

Also automatic: voice time is tracked, a break nudge arrives after 3 hours, level-ups are announced, and the
bot posts a daily challenge, tech news, an evening goal check-in and a weekly recap.

## XP, levels and rewards

- 1 XP per minute in voice or focus sessions, +10 for finishing a focus session, +30 for completing your daily goal
  (once a day), a daily bonus (25-60 XP), and +2 per correct quiz answer.
- Level N needs `50 x (N-1)^2` XP in total. Titles: Newcomer, Warming Up, Getting Serious, Grinder, Locked In,
  Deep Worker, Centurion, Veteran, Master, Legend, Titan, Immortal.
- Level-ups are announced in a channel. Admins can switch on **title roles** (a role per title) in `/wizard`.

## 24/7 Pomodoro rooms

Voice channels that run a nonstop focus/break cycle (for example 25/5 or 50/10). The cycle is aligned to the clock,
so every room with the same lengths stays in sync. The bot posts each phase in the room's text chat whenever
people are inside, and tells newcomers what phase it is. Time in these rooms counts toward stats and XP.

## Admin setup in two minutes

1. Create the bot in the Discord developer portal and invite it (see below).
2. In your server run **`/wizard`** and press **⚡ Auto-setup everything**. It creates a *🧠 Productivity* category with
   `#hub`, `#welcome`, `#roles`, `#daily-goals`, `#daily-threads`, `#tech-news`, `#daily-challenge`, `#weekly-recap`,
   `#level-ups`, a **➕ Create a study room** voice lobby and two Pomodoro rooms; saves every setting; and posts the
   button panel and the role menu. It is safe to run again (it reuses what exists).
3. Drag the bot's role **above** the roles it should hand out (Server Settings > Roles), and pin the panel in `#hub`.

Prefer to do it by hand? `/wizard` also has **Choose channels manually**, or use `/setup channel ...`, `/setup lobby`,
`/pomodoro add`, `/xp channel`, `/xp roles`, `/panel` (posts the panel in the current channel) and `/roles panel`.

## All slash commands

| Area | Commands |
|---|---|
| Menu | `/menu`, `/panel` (admin), `/wizard` (admin), `/help` |
| Focus | `/focus start [minutes] [break]`, `/focus stop`, `/focus status` |
| Progress | `/stats [member]`, `/leaderboard [period]`, `/levels`, `/daily`, `/nudges on\|off` |
| Accountability | `/goal set\|done\|view`, `/todo add\|list\|done\|remove\|clear_done\|visibility`, `/partner join\|status\|leave` |
| Tech | `/run`, `/quiz`, `/explain`, `/snippet save\|get\|list\|delete`, `/docs`, `/review`, `/challenge today`, `/news`, `/trending`, `/github`, `/pypi`, `/resources` |
| Flashcards | `/flashcards add\|review\|list\|delete` |
| Environment | `/remind add\|list\|cancel`, `/room lock\|unlock\|invite\|rename\|limit`, `/quiet on\|off`, `/ambient`, `/poll` |
| Admin | `/setup channel\|lobby\|track_add\|track_remove\|track_clear\|automod\|show`, `/roles panel`, `/pomodoro add\|remove\|list`, `/xp channel\|roles` |

To-do lists are private by default. `/todo visibility public` lets members view yours with `/todo list member:@you`.

## 1. Create the Discord application

1. Go to <https://discord.com/developers/applications> and click **New Application**.
2. **Bot** tab: click **Reset Token** and copy it (this is `DISCORD_TOKEN`).
3. On the same tab turn on **Server Members Intent** and **Message Content Intent**, then **Save Changes**.
   The bot will not start without them.
4. **OAuth2 > URL Generator**: scopes `bot` and `applications.commands`. Permissions: View Channels, Send Messages,
   Send Messages in Threads, Create Public Threads, Embed Links, Attach Files, Read Message History, Add Reactions,
   Manage Messages, Manage Roles, Manage Channels, Move Members, Moderate Members, Connect.
   (Attach Files is needed for the stats card image.) Or use this link with your Application ID:
   `https://discord.com/oauth2/authorize?client_id=YOUR_APP_ID&permissions=1409035660368&scope=bot%20applications.commands`
5. Open the link and add the bot to your server.

## 2. Run locally (optional)

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # then paste your token into .env
python bot.py
```

Tip: set `DEV_GUILD_ID` to your test server's ID while developing so slash commands update instantly.

## 3. Deploy on Railway

1. Push this folder to a GitHub repository (the `.gitignore` already keeps `.env` and the database out).
2. On <https://railway.com>: **New Project > Deploy from GitHub repo** and pick the repo.
3. Open the service > **Variables** and add:
   - `DISCORD_TOKEN` = your bot token
   - `DB_PATH` = `/data/bot.db`
   - `TIMEZONE` = e.g. `Asia/Kolkata`
4. **Add a Volume** to the service and set the mount path to `/data`.
   Without a volume your stats, XP, goals and flashcards are wiped on every redeploy.
5. Deploy. The bot has no web server, so Railway treats it as a background worker. Check **Deploy Logs** for `Logged in as ...`.

### Python version

The project pins **Python 3.12** in `.python-version` (there is no "Python 22"; current releases are 3.12 to 3.14).
To change it on Railway, set `RAILPACK_PYTHON_VERSION` (or edit `.python-version`).

### About Railway's "free" tier

Railway gives new accounts a one-time $5 trial credit (30 days), then a Free plan with $1 of usage credit per month
(0.5 GB RAM, 0.5 GB volume per service). A bot like this uses little CPU and roughly 100-200 MB of RAM (a bit more
while rendering stats cards), so it may sit near or above the free monthly credit; if it runs out, the service stops
until the next month. The Hobby plan is $5/month with $5 of usage included. See <https://docs.railway.com/pricing/plans>.

## Configuration reference

All settings are environment variables (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `DISCORD_TOKEN` | required | Bot token |
| `DB_PATH` | `data/bot.db` | SQLite file location (use `/data/bot.db` with a Railway volume) |
| `TIMEZONE` | `Asia/Kolkata` | Defines "today" for streaks and when scheduled posts go out |
| `CHECKIN_HOUR`, `NEWS_HOUR`, `CHALLENGE_HOUR`, `DAILY_HOUR` | 21, 8, 9, 9 | Hour (0-23) for each scheduled post |
| `RECAP_WEEKDAY`, `RECAP_HOUR` | 6, 20 | Weekly recap day (0=Mon ... 6=Sun) and hour |
| `DEV_GUILD_ID` | empty | Instant command sync to one server (development only) |
| `SYNC_COMMANDS` | `1` | Set `0` to skip command sync on startup |
| `AI_API_KEY`, `AI_BASE_URL`, `AI_MODEL` | empty | Enables `/explain` and `/quiz` with any OpenAI-compatible API |
| `GITHUB_TOKEN` | empty | Optional, raises the GitHub API rate limit |
| `PISTON_URL`, `PISTON_API_KEY` | public Piston, empty | Code runner for `/run`. Empty `PISTON_URL` turns it off |

## Enabling the AI features (`/explain`, `/quiz`)

Pick any provider with an OpenAI-compatible API (several offer a free tier) and set three variables, for example:

```
AI_BASE_URL=https://api.groq.com/openai/v1
AI_API_KEY=...your key...
AI_MODEL=...a model name from that provider...
```

Without them the AI buttons say they are not configured. Flashcards, XP and everything else work without AI.

## Notes and limits

- **Restarts:** running focus sessions live in memory, so a redeploy ends them (their buttons stop working).
  Everything else is stored in SQLite. Button menus you opened earlier stop responding after a restart; reopen with `/menu`.
- **Code runner:** `/run` depends on a Piston-compatible service. If the default public server stops accepting
  requests, set `PISTON_URL` (and `PISTON_API_KEY` if it needs one) or host your own.
- **Time tracking** counts every voice channel except AFK and the study-room lobby. Use `/setup track_add` to count
  only specific channels. Temporary study rooms and Pomodoro rooms always count.
- **Moderators** (anyone with Manage Messages) are exempt from the spam and link filters.
- Slash command changes can take a few minutes to appear globally. `DEV_GUILD_ID` makes them instant on one server.

## Project layout

```
bot.py                  entry point, loads the cogs, syncs commands
config.py  utils.py     env settings and helpers
leveling.py             XP / level maths and the safe grant() helper
cardgen.py              Pillow image generation (stats card + heatmap)
db.py                   SQLite schema and queries
challenges.py           daily challenge rotation
cogs/panel.py           button menu (hub, sub-menus, forms)
cogs/wizard.py          /wizard one-click server setup
cogs/focus.py           focus sessions
cogs/pomodoro.py        24/7 Pomodoro voice rooms
cogs/tracking.py        voice time, stats, leaderboard, recap, nudges
cogs/levels.py          XP, level-ups, role rewards, daily bonus
cogs/accountability.py  goals, check-ins, to-dos, partners
cogs/flashcards.py      spaced-repetition flashcards, AI quiz
cogs/tech.py            snippets, docs, AI helper, challenge, news, trending, resources, review
cogs/dev.py             /run, /github, /pypi
cogs/environment.py     reminders, study rooms, quiet hours, ambient, poll, daily thread
cogs/admin.py           setup, role panel, welcome, automod, help
```
