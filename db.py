"""SQLite storage layer (aiosqlite)."""
from __future__ import annotations

import os
from datetime import date, timedelta
from typing import Any, NamedTuple

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    val TEXT
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id          INTEGER PRIMARY KEY,
    checkin_channel   INTEGER,
    recap_channel     INTEGER,
    news_channel      INTEGER,
    challenge_channel INTEGER,
    daily_channel     INTEGER,
    welcome_channel   INTEGER,
    lobby_channel     INTEGER,
    spam_filter       INTEGER DEFAULT 1,
    link_filter       INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tracked_channels (
    guild_id   INTEGER,
    channel_id INTEGER,
    PRIMARY KEY (guild_id, channel_id)
);

CREATE TABLE IF NOT EXISTS study_time (
    guild_id      INTEGER,
    user_id       INTEGER,
    day           TEXT,
    seconds       INTEGER DEFAULT 0,
    focus_seconds INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day)
);

CREATE TABLE IF NOT EXISTS focus_sessions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id     INTEGER,
    user_id      INTEGER,
    minutes      INTEGER,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS todos (
    user_id    INTEGER,
    n          INTEGER,
    text       TEXT,
    done       INTEGER DEFAULT 0,
    created_at TEXT,
    PRIMARY KEY (user_id, n)
);

CREATE TABLE IF NOT EXISTS goals (
    guild_id INTEGER,
    user_id  INTEGER,
    day      TEXT,
    text     TEXT,
    done     INTEGER DEFAULT 0,
    PRIMARY KEY (guild_id, user_id, day)
);

CREATE TABLE IF NOT EXISTS reminders (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER,
    guild_id   INTEGER,
    channel_id INTEGER,
    message    TEXT,
    due_ts     INTEGER
);

CREATE TABLE IF NOT EXISTS snippets (
    guild_id   INTEGER,
    name       TEXT,
    user_id    INTEGER,
    language   TEXT,
    code       TEXT,
    created_at TEXT,
    PRIMARY KEY (guild_id, name)
);

CREATE TABLE IF NOT EXISTS temp_rooms (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER,
    owner_id   INTEGER
);

CREATE TABLE IF NOT EXISTS quiet_channels (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER,
    unlock_ts  INTEGER,
    prev       INTEGER
);

CREATE TABLE IF NOT EXISTS partner_queue (
    guild_id INTEGER,
    user_id  INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS partners (
    guild_id INTEGER,
    user_id  INTEGER,
    partner_id INTEGER,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS user_prefs (
    user_id     INTEGER PRIMARY KEY,
    nudges      INTEGER DEFAULT 1,
    todo_public INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS guild_config (
    guild_id INTEGER,
    key      TEXT,
    value    TEXT,
    PRIMARY KEY (guild_id, key)
);

CREATE TABLE IF NOT EXISTS xp (
    guild_id INTEGER,
    user_id  INTEGER,
    xp       REAL DEFAULT 0,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS daily_claims (
    guild_id INTEGER,
    user_id  INTEGER,
    day      TEXT,
    PRIMARY KEY (guild_id, user_id, day)
);

CREATE TABLE IF NOT EXISTS pomodoro_rooms (
    channel_id INTEGER PRIMARY KEY,
    guild_id   INTEGER,
    focus_min  INTEGER,
    break_min  INTEGER
);

CREATE TABLE IF NOT EXISTS cards (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    front   TEXT,
    back    TEXT,
    box     INTEGER DEFAULT 1,
    due_ts  INTEGER
);
"""

CHANNEL_KINDS = {
    "checkin": "checkin_channel",
    "recap": "recap_channel",
    "news": "news_channel",
    "challenge": "challenge_channel",
    "daily": "daily_channel",
    "welcome": "welcome_channel",
}
SETTING_FIELDS = set(CHANNEL_KINDS.values()) | {"lobby_channel", "spam_filter", "link_filter"}


class Result(NamedTuple):
    rowcount: int
    lastrowid: int | None


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        folder = os.path.dirname(self.path)
        if folder:
            os.makedirs(folder, exist_ok=True)
        self.conn = await aiosqlite.connect(self.path)
        self.conn.row_factory = aiosqlite.Row
        await self.conn.execute("PRAGMA journal_mode=WAL")
        await self.conn.executescript(SCHEMA)
        # Lightweight migration for databases created before todo visibility existed.
        cols = [r["name"] for r in await self.fetchall("PRAGMA table_info(user_prefs)")]
        if "todo_public" not in cols:
            await self.conn.execute("ALTER TABLE user_prefs ADD COLUMN todo_public INTEGER DEFAULT 0")
        await self.conn.commit()

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    # ---- generic helpers -------------------------------------------------
    async def execute(self, sql: str, params: tuple | list = ()) -> Result:
        assert self.conn is not None, "Database not connected"
        async with self.conn.execute(sql, params) as cur:
            result = Result(cur.rowcount, cur.lastrowid)
        await self.conn.commit()
        return result

    async def fetchone(self, sql: str, params: tuple | list = ()) -> aiosqlite.Row | None:
        assert self.conn is not None, "Database not connected"
        async with self.conn.execute(sql, params) as cur:
            return await cur.fetchone()

    async def fetchall(self, sql: str, params: tuple | list = ()) -> list[aiosqlite.Row]:
        assert self.conn is not None, "Database not connected"
        async with self.conn.execute(sql, params) as cur:
            return list(await cur.fetchall())

    async def scalar(self, sql: str, params: tuple | list = ()) -> Any:
        row = await self.fetchone(sql, params)
        return row[0] if row else None

    async def claim(self, key: str, value: str) -> bool:
        """Atomically store value for key. True only if the stored value changed
        (used to run a scheduled job at most once per day, even across restarts)."""
        res = await self.execute(
            "INSERT INTO kv (key, val) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET val = excluded.val WHERE val <> excluded.val",
            (key, value),
        )
        return res.rowcount > 0

    # ---- guild settings --------------------------------------------------
    async def get_settings(self, guild_id: int) -> dict[str, Any]:
        row = await self.fetchone("SELECT * FROM guild_settings WHERE guild_id=?", (guild_id,))
        if row is None:
            data: dict[str, Any] = {f: None for f in SETTING_FIELDS}
            data.update(guild_id=guild_id, spam_filter=1, link_filter=1)
            return data
        return dict(row)

    async def set_setting(self, guild_id: int, field: str, value: Any) -> None:
        if field not in SETTING_FIELDS:
            raise ValueError(f"unknown setting {field!r}")
        await self.execute("INSERT OR IGNORE INTO guild_settings (guild_id) VALUES (?)", (guild_id,))
        await self.execute(f"UPDATE guild_settings SET {field}=? WHERE guild_id=?", (value, guild_id))

    # ---- study time ------------------------------------------------------
    async def add_study(self, guild_id: int, user_id: int, day: str, seconds: int, focus_seconds: int = 0) -> None:
        await self.execute(
            "INSERT INTO study_time (guild_id, user_id, day, seconds, focus_seconds) VALUES (?,?,?,?,?) "
            "ON CONFLICT(guild_id, user_id, day) DO UPDATE SET "
            "seconds = seconds + excluded.seconds, focus_seconds = focus_seconds + excluded.focus_seconds",
            (guild_id, user_id, day, seconds, focus_seconds),
        )

    async def record_focus(self, guild_id: int, user_id: int, minutes: int, completed_at: str) -> None:
        await self.execute(
            "INSERT INTO focus_sessions (guild_id, user_id, minutes, completed_at) VALUES (?,?,?,?)",
            (guild_id, user_id, minutes, completed_at),
        )

    async def user_totals(self, guild_id: int, user_id: int, today: date) -> dict[str, int]:
        week_start = (today - timedelta(days=6)).isoformat()
        month_start = today.replace(day=1).isoformat()
        row = await self.fetchone(
            """SELECT
                 COALESCE(SUM(CASE WHEN day = ? THEN seconds END), 0)  AS today,
                 COALESCE(SUM(CASE WHEN day >= ? THEN seconds END), 0) AS week,
                 COALESCE(SUM(CASE WHEN day >= ? THEN seconds END), 0) AS month,
                 COALESCE(SUM(seconds), 0)                             AS total,
                 COALESCE(SUM(focus_seconds), 0)                       AS focus_total,
                 COALESCE(MAX(seconds), 0)                             AS best_day
               FROM study_time WHERE guild_id = ? AND user_id = ?""",
            (today.isoformat(), week_start, month_start, guild_id, user_id),
        )
        return dict(row) if row else {}

    async def focus_session_count(self, guild_id: int, user_id: int) -> int:
        return int(await self.scalar(
            "SELECT COUNT(*) FROM focus_sessions WHERE guild_id=? AND user_id=?", (guild_id, user_id)
        ) or 0)

    async def leaderboard(self, guild_id: int, since_day: str, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT user_id, SUM(seconds) AS total FROM study_time "
            "WHERE guild_id = ? AND day >= ? GROUP BY user_id HAVING total > 0 "
            "ORDER BY total DESC LIMIT ?",
            (guild_id, since_day, limit),
        )

    async def streaks(self, guild_id: int, user_id: int, today: date, min_seconds: int = 300) -> tuple[int, int]:
        """(current_streak, best_streak) counting days with at least min_seconds of study."""
        rows = await self.fetchall(
            "SELECT day FROM study_time WHERE guild_id=? AND user_id=? AND seconds >= ?",
            (guild_id, user_id, min_seconds),
        )
        days = sorted({date.fromisoformat(r["day"]) for r in rows})
        if not days:
            return 0, 0
        day_set = set(days)
        best = run = 0
        prev: date | None = None
        for d in days:
            run = run + 1 if prev is not None and (d - prev).days == 1 else 1
            best = max(best, run)
            prev = d
        cursor = today if today in day_set else today - timedelta(days=1)
        current = 0
        while cursor in day_set:
            current += 1
            cursor -= timedelta(days=1)
        return current, best

    # ---- goals -----------------------------------------------------------
    async def set_goal(self, guild_id: int, user_id: int, day: str, text: str) -> None:
        await self.execute(
            "INSERT INTO goals (guild_id, user_id, day, text, done) VALUES (?,?,?,?,0) "
            "ON CONFLICT(guild_id, user_id, day) DO UPDATE SET text = excluded.text, done = 0",
            (guild_id, user_id, day, text),
        )

    async def daily_seconds(self, guild_id: int, user_id: int, since_day: str) -> dict[str, int]:
        """{'2026-10-24': seconds, ...} for every day on/after since_day."""
        rows = await self.fetchall(
            "SELECT day, seconds FROM study_time WHERE guild_id=? AND user_id=? AND day>=?",
            (guild_id, user_id, since_day),
        )
        return {r["day"]: int(r["seconds"]) for r in rows}

    # ---- per-guild config (small key/value settings) ----------------------
    async def get_config(self, guild_id: int, key: str, default: str | None = None) -> str | None:
        value = await self.scalar("SELECT value FROM guild_config WHERE guild_id=? AND key=?", (guild_id, key))
        return default if value is None else value

    async def set_config(self, guild_id: int, key: str, value: Any) -> None:
        await self.execute(
            "INSERT INTO guild_config (guild_id, key, value) VALUES (?,?,?) "
            "ON CONFLICT(guild_id, key) DO UPDATE SET value = excluded.value",
            (guild_id, key, str(value)),
        )

    # ---- XP ---------------------------------------------------------------
    async def add_xp(self, guild_id: int, user_id: int, amount: float) -> float:
        """Add XP and return the new total."""
        await self.execute(
            "INSERT INTO xp (guild_id, user_id, xp) VALUES (?,?,?) "
            "ON CONFLICT(guild_id, user_id) DO UPDATE SET xp = xp + excluded.xp",
            (guild_id, user_id, amount),
        )
        return await self.get_xp(guild_id, user_id)

    async def get_xp(self, guild_id: int, user_id: int) -> float:
        return float(await self.scalar("SELECT xp FROM xp WHERE guild_id=? AND user_id=?", (guild_id, user_id)) or 0)

    async def top_xp(self, guild_id: int, limit: int = 10) -> list[aiosqlite.Row]:
        return await self.fetchall(
            "SELECT user_id, xp FROM xp WHERE guild_id=? AND xp>0 ORDER BY xp DESC LIMIT ?", (guild_id, limit)
        )

    async def claim_daily(self, guild_id: int, user_id: int, day: str) -> bool:
        res = await self.execute(
            "INSERT OR IGNORE INTO daily_claims (guild_id, user_id, day) VALUES (?,?,?)", (guild_id, user_id, day)
        )
        return res.rowcount > 0

    async def daily_claim_streak(self, guild_id: int, user_id: int, today: date) -> int:
        """Consecutive days (ending today) on which the daily bonus was claimed."""
        since = (today - timedelta(days=60)).isoformat()
        rows = await self.fetchall(
            "SELECT day FROM daily_claims WHERE guild_id=? AND user_id=? AND day>=?", (guild_id, user_id, since)
        )
        days = {r["day"] for r in rows}
        streak, cursor = 0, today
        while cursor.isoformat() in days:
            streak += 1
            cursor -= timedelta(days=1)
        return streak
