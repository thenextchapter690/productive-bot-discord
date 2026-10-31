"""Central configuration, read from environment variables (a .env file is supported)."""
from __future__ import annotations

import os
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
DB_PATH = os.getenv("DB_PATH", "data/bot.db").strip() or "data/bot.db"

TIMEZONE = os.getenv("TIMEZONE", "Asia/Kolkata").strip() or "Asia/Kolkata"
try:
    TZ = ZoneInfo(TIMEZONE)
except Exception:  # unknown timezone name or missing tzdata
    TIMEZONE = "UTC"
    TZ = ZoneInfo("UTC")

DEV_GUILD_ID = _int("DEV_GUILD_ID", 0)
SYNC_COMMANDS = os.getenv("SYNC_COMMANDS", "1").strip() != "0"

# Scheduled posts (hour of day, 0-23, in TIMEZONE)
CHECKIN_HOUR = _int("CHECKIN_HOUR", 21)
NEWS_HOUR = _int("NEWS_HOUR", 8)
CHALLENGE_HOUR = _int("CHALLENGE_HOUR", 9)
DAILY_HOUR = _int("DAILY_HOUR", 9)
RECAP_WEEKDAY = _int("RECAP_WEEKDAY", 6)  # 0=Monday ... 6=Sunday
RECAP_HOUR = _int("RECAP_HOUR", 20)

# Optional AI helper (any OpenAI-compatible /chat/completions endpoint)
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip()
AI_MODEL = os.getenv("AI_MODEL", "").strip()
AI_ENABLED = bool(AI_API_KEY and AI_BASE_URL and AI_MODEL)

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "").strip()

# Optional code runner (/run). Any Piston-compatible endpoint works; empty disables the feature.
PISTON_URL = os.getenv("PISTON_URL", "https://emkc.org/api/v2/piston").strip().rstrip("/")
PISTON_API_KEY = os.getenv("PISTON_API_KEY", "").strip()
