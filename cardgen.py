"""Image generation (Pillow): the stats card with a study-activity heatmap."""
from __future__ import annotations

import io
from datetime import date, timedelta

from PIL import Image, ImageDraw, ImageFont

W, H = 940, 580
BG = (24, 25, 38)
PANEL = (36, 38, 58)
TEXT = (240, 241, 250)
MUTED = (150, 154, 184)
ACCENT = (110, 125, 255)
BAR_BG = (52, 55, 80)
HEAT = [(48, 51, 74), (34, 96, 88), (37, 142, 99), (62, 192, 112), (118, 242, 146)]
HEAT_STEPS = [0, 900, 3600, 7200]  # seconds: <15m, <1h, <2h, 2h+

WEEKS = 26

_REG = [
    "DejaVuSans.ttf", "arial.ttf", "Arial.ttf", "LiberationSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/System/Library/Fonts/Supplemental/Arial.ttf",
]
_BOLD = [
    "DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_font_cache: dict[tuple[int, bool], ImageFont.ImageFont] = {}


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    key = (size, bold)
    if key in _font_cache:
        return _font_cache[key]
    found = None
    for name in _BOLD if bold else _REG:
        try:
            found = ImageFont.truetype(name, size)
            break
        except OSError:
            continue
    if found is None:
        try:
            found = ImageFont.load_default(size=size)
        except TypeError:  # very old Pillow
            found = ImageFont.load_default()
    _font_cache[key] = found
    return found


def fmt_dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes = rem // 60
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m"


def heat_level(seconds: int) -> int:
    level = 0
    for i, limit in enumerate(HEAT_STEPS):
        if seconds > limit:
            level = i + 1
    return level


def _avatar(avatar_bytes: bytes | None, size: int, name: str) -> Image.Image:
    img = None
    if avatar_bytes:
        try:
            img = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((size, size), Image.LANCZOS)
        except Exception:
            img = None
    if img is None:
        img = Image.new("RGBA", (size, size), ACCENT + (255,))
        d = ImageDraw.Draw(img)
        letter = (name[:1] or "?").upper()
        f = font(size // 2, bold=True)
        w = d.textlength(letter, font=f)
        d.text(((size - w) / 2, size * 0.2), letter, font=f, fill=TEXT)
    scale = 4
    mask = Image.new("L", (size * scale, size * scale), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, size * scale - 1, size * scale - 1), fill=255)
    mask = mask.resize((size, size), Image.LANCZOS)
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def _fit(draw: ImageDraw.ImageDraw, text: str, f, max_width: int) -> str:
    if draw.textlength(text, font=f) <= max_width:
        return text
    while text and draw.textlength(text + "...", font=f) > max_width:
        text = text[:-1]
    return text + "..."


def render_stats_card(
    *,
    name: str,
    avatar_bytes: bytes | None,
    level: int,
    title: str,
    xp_into: float,
    xp_needed: int,
    xp_total: float,
    today_seconds: int,
    week_seconds: int,
    total_seconds: int,
    streak: int,
    best_streak: int,
    daily: dict[str, int],
    today: date,
) -> bytes:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)

    # ---- header -----------------------------------------------------------
    av = _avatar(avatar_bytes, 112, name)
    img.paste(av, (40, 36), av)
    d.text((176, 34), _fit(d, name, font(36, True), 720), font=font(36, True), fill=TEXT)
    d.text((176, 86), f"Level {level}  -  {title}", font=font(22), fill=ACCENT)

    bx, by, bw, bh = 176, 124, 724, 20
    d.rounded_rectangle((bx, by, bx + bw, by + bh), radius=10, fill=BAR_BG)
    fraction = 0.0 if xp_needed <= 0 else min(1.0, xp_into / xp_needed)
    if fraction > 0:
        d.rounded_rectangle((bx, by, bx + max(bh, int(bw * fraction)), by + bh), radius=10, fill=ACCENT)
    d.text(
        (bx, by + 28),
        f"{xp_into:,.0f} / {xp_needed:,} XP to level {level + 1}   -   {xp_total:,.0f} XP total",
        font=font(16), fill=MUTED,
    )

    # ---- stat boxes -------------------------------------------------------
    boxes = [
        ("TODAY", fmt_dur(today_seconds)),
        ("LAST 7 DAYS", fmt_dur(week_seconds)),
        ("ALL TIME", fmt_dur(total_seconds)),
        ("STREAK", f"{streak} day{'s' if streak != 1 else ''}"),
    ]
    box_w, gap, top = 203, 16, 190
    for i, (label, value) in enumerate(boxes):
        x = 40 + i * (box_w + gap)
        d.rounded_rectangle((x, top, x + box_w, top + 84), radius=14, fill=PANEL)
        d.text((x + 18, top + 12), label, font=font(14, True), fill=MUTED)
        d.text((x + 18, top + 36), value, font=font(30, True), fill=TEXT)
        if label == "STREAK":
            best = f"best {best_streak}"
            d.text((x + box_w - 18 - d.textlength(best, font=font(13)), top + 13), best, font=font(13), fill=MUTED)

    # ---- heatmap ----------------------------------------------------------
    py = 296
    d.rounded_rectangle((40, py, W - 40, H - 30), radius=16, fill=PANEL)
    d.text((60, py + 14), f"STUDY ACTIVITY  -  LAST {WEEKS} WEEKS", font=font(14, True), fill=MUTED)

    start = today - timedelta(days=today.weekday() + 7 * (WEEKS - 1))  # a Monday
    gx, gy, cell, pitch = 100, py + 62, 22, 26
    active = 0
    last_month = None
    last_label_col = -10
    for col in range(WEEKS):
        week_start = start + timedelta(days=col * 7)
        # label a month where it begins, but never closer than 3 columns to the previous label
        if week_start.month != last_month and col - last_label_col >= 3 and (col == 0 or week_start.day <= 7):
            d.text((gx + col * pitch, gy - 22), week_start.strftime("%b"), font=font(13), fill=MUTED)
            last_label_col = col
        last_month = week_start.month
        for row in range(7):
            day = week_start + timedelta(days=row)
            if day > today:
                continue
            secs = daily.get(day.isoformat(), 0)
            if secs >= 300:
                active += 1
            x, y = gx + col * pitch, gy + row * pitch
            d.rounded_rectangle((x, y, x + cell, y + cell), radius=5, fill=HEAT[heat_level(secs)])
    for row, label in ((0, "Mon"), (2, "Wed"), (4, "Fri")):
        d.text((60, gy + row * pitch + 3), label, font=font(13), fill=MUTED)

    summary = f"{active} active days"
    d.text((W - 60 - d.textlength(summary, font=font(14)), py + 14), summary, font=font(14), fill=MUTED)

    lx = gx + WEEKS * pitch + 22
    d.text((lx, gy - 2), "Less", font=font(13), fill=MUTED)
    for i, color in enumerate(HEAT):
        y = gy + 22 + i * 28
        d.rounded_rectangle((lx, y, lx + cell, y + cell), radius=5, fill=color)
    d.text((lx, gy + 22 + 5 * 28 + 2), "More", font=font(13), fill=MUTED)

    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return out.getvalue()
