"""Small shared helpers."""
from __future__ import annotations

import re
from datetime import date, datetime

import config

BRAND = 0x5865F2
GREEN = 0x57F287
ORANGE = 0xFEE75C
RED = 0xED4245
PURPLE = 0x9B59B6

_DUR_PART = re.compile(r"(\d+)([smhdw])")
_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def now_local() -> datetime:
    return datetime.now(config.TZ)


def today_local() -> date:
    return now_local().date()


def today_str() -> str:
    return today_local().isoformat()


def parse_duration(text: str) -> int | None:
    """'90' -> 5400 (bare numbers are minutes), '1h30m' -> 5400, '2d' -> 172800. None if invalid."""
    cleaned = text.strip().lower().replace(" ", "")
    if not cleaned:
        return None
    if cleaned.isdigit():
        return int(cleaned) * 60
    parts = _DUR_PART.findall(cleaned)
    if not parts or "".join(f"{n}{u}" for n, u in parts) != cleaned:
        return None
    return sum(int(n) * _UNITS[u] for n, u in parts)


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m"
    return f"{seconds}s"


def progress_bar(fraction: float, width: int = 10) -> str:
    fraction = min(1.0, max(0.0, fraction))
    filled = int(round(fraction * width))
    return "▰" * filled + "▱" * (width - filled)


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def chunk(text: str, size: int = 1900) -> list[str]:
    """Split text into <=size pieces, preferring newline boundaries."""
    pieces: list[str] = []
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        pieces.append(text[:cut])
        text = text[cut:].lstrip("\n")
    if text:
        pieces.append(text)
    return pieces
