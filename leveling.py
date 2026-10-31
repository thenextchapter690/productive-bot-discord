"""XP and level maths (pure functions) plus a safe helper other cogs use to hand out XP.

1 XP is earned per minute of study, with bonuses for completing focus sessions, goals,
the daily reward and quizzes. Level N needs 50 * (N-1)^2 XP in total.
"""
from __future__ import annotations

import logging
import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from discord.ext import commands

log = logging.getLogger(__name__)

# (first level that uses the title, title). Each title can come with a role reward.
TITLES = [
    (1, "Newcomer"),
    (2, "Warming Up"),
    (4, "Getting Serious"),
    (6, "Grinder"),
    (9, "Locked In"),
    (13, "Deep Worker"),
    (18, "Centurion"),
    (25, "Veteran"),
    (33, "Master"),
    (42, "Legend"),
    (55, "Titan"),
    (70, "Immortal"),
]
TITLE_NAMES = [t for _, t in TITLES]

# Bonus amounts
XP_FOCUS_COMPLETE = 10
XP_GOAL_DONE = 30
XP_QUIZ_CORRECT = 2
XP_DAILY_BASE = 25
XP_DAILY_PER_STREAK_DAY = 5
XP_DAILY_MAX_STREAK = 7


def xp_for_level(level: int) -> int:
    """Total XP needed to reach `level` (level 1 needs 0)."""
    return 50 * (max(level, 1) - 1) ** 2


def level_for_xp(xp: float) -> int:
    return math.isqrt(int(max(xp, 0)) // 50) + 1


def title_for_level(level: int) -> str:
    title = TITLES[0][1]
    for first_level, name in TITLES:
        if level >= first_level:
            title = name
    return title


def progress(xp: float) -> tuple[int, str, float, int, float]:
    """(level, title, xp_into_level, xp_needed_for_next, fraction 0-1)."""
    level = level_for_xp(xp)
    floor = xp_for_level(level)
    needed = xp_for_level(level + 1) - floor
    into = max(0.0, xp - floor)
    return level, title_for_level(level), into, needed, min(1.0, into / needed)


def daily_bonus(streak_days: int) -> int:
    return XP_DAILY_BASE + XP_DAILY_PER_STREAK_DAY * min(max(streak_days, 1), XP_DAILY_MAX_STREAK)


async def grant(bot: "commands.Bot", guild_id: int, user_id: int, amount: float, reason: str = "") -> None:
    """Give XP via the Levels cog. Never raises: XP is a bonus, it must not break the feature that called it."""
    if amount <= 0:
        return
    cog = bot.get_cog("Levels")
    if cog is None:
        return
    try:
        await cog.award(guild_id, user_id, amount, reason)  # type: ignore[attr-defined]
    except Exception:
        log.exception("Could not grant XP")
