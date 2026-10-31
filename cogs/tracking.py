"""Voice-time tracking, stats, levels/badges, leaderboards, streaks, break nudges, weekly recap."""
from __future__ import annotations

import asyncio
import io
import logging
import time
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import leveling
import utils

log = logging.getLogger(__name__)

NUDGE_EVERY = 3 * 3600  # remind people to take a break every 3 hours of continuous voice time

# (hours required, title)
LEVELS = [
    (0, "Newcomer"), (2, "Warming Up"), (5, "Getting Serious"), (10, "Grinder"),
    (25, "Locked In"), (50, "Deep Worker"), (100, "Centurion"), (200, "Veteran"),
    (350, "Master"), (500, "Legend"), (750, "Titan"), (1000, "Immortal"),
]


def level_for(hours: float) -> tuple[int, str, float | None]:
    idx = max(i for i, (needed, _) in enumerate(LEVELS) if hours >= needed)
    next_needed = LEVELS[idx + 1][0] if idx + 1 < len(LEVELS) else None
    return idx + 1, LEVELS[idx][1], next_needed


def badges_for(hours: float, streak: int, best_streak: int, sessions: int) -> list[str]:
    out: list[str] = []
    top_streak = max(streak, best_streak)
    if top_streak >= 3:
        out.append("🔥 3-day streak")
    if top_streak >= 7:
        out.append("📅 Week warrior")
    if top_streak >= 30:
        out.append("🏆 Month master")
    if hours >= 10:
        out.append("⏱️ 10 hours")
    if hours >= 100:
        out.append("💯 100 hours")
    if sessions >= 10:
        out.append("🎯 Focus rookie")
    if sessions >= 100:
        out.append("🤖 Focus machine")
    return out


class Tracking(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # (guild_id, user_id) -> {"last": ts of last credit, "start": ts of continuous stay, "next_nudge": ts}
        self.active: dict[tuple[int, int], dict[str, float]] = {}

    async def cog_load(self) -> None:
        self.credit_loop.start()
        self.recap_loop.start()

    async def cog_unload(self) -> None:
        self.credit_loop.cancel()
        self.recap_loop.cancel()
        now = time.time()
        for key in list(self.active):
            await self._credit(key, now, close=True)

    # ---- tracking core ---------------------------------------------------
    async def is_tracked_channel(self, channel: discord.abc.GuildChannel | None) -> bool:
        if channel is None:
            return False
        guild = channel.guild
        if guild.afk_channel and channel.id == guild.afk_channel.id:
            return False
        db = self.bot.db
        if await db.scalar("SELECT 1 FROM temp_rooms WHERE channel_id=?", (channel.id,)):
            return True  # temporary study rooms always count
        if await db.scalar("SELECT 1 FROM tracked_channels WHERE guild_id=? LIMIT 1", (guild.id,)):
            return bool(await db.scalar(
                "SELECT 1 FROM tracked_channels WHERE guild_id=? AND channel_id=?", (guild.id, channel.id)
            ))
        settings = await db.get_settings(guild.id)
        return channel.id != settings["lobby_channel"]  # everything except the "create room" lobby

    async def is_tracked_voice(self, member: discord.Member) -> bool:
        voice = member.voice
        return bool(voice and voice.channel and await self.is_tracked_channel(voice.channel))

    async def _credit(self, key: tuple[int, int], now: float, close: bool = False) -> None:
        info = self.active.get(key)
        if info is None:
            return
        delta = int(now - info["last"])
        info["last"] = now
        if delta > 0:
            day = datetime.fromtimestamp(now, config.TZ).date().isoformat()
            await self.bot.db.add_study(key[0], key[1], day, delta)
            await leveling.grant(self.bot, key[0], key[1], delta / 60, "voice")
        if close:
            self.active.pop(key, None)

    def _start(self, key: tuple[int, int]) -> None:
        now = time.time()
        self.active.setdefault(key, {"last": now, "start": now, "next_nudge": now + NUDGE_EVERY})

    @commands.Cog.listener()
    async def on_ready(self) -> None:
        # Pick up people who are already in voice (e.g. after a restart).
        for guild in self.bot.guilds:
            for vc in guild.voice_channels:
                if not await self.is_tracked_channel(vc):
                    continue
                for member in vc.members:
                    if not member.bot:
                        self._start((guild.id, member.id))

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        key = (member.guild.id, member.id)
        was = await self.is_tracked_channel(before.channel)
        now_tracked = await self.is_tracked_channel(after.channel)
        if was and not now_tracked:
            await self._credit(key, time.time(), close=True)
        elif now_tracked and not was:
            self._start(key)

    @tasks.loop(minutes=5)
    async def credit_loop(self) -> None:
        now = time.time()
        for key in list(self.active):
            await self._credit(key, now)
            info = self.active.get(key)
            if info and now >= info["next_nudge"]:
                info["next_nudge"] = now + NUDGE_EVERY
                await self._nudge(key, now - info["start"])

    @credit_loop.before_loop
    async def _before_credit(self) -> None:
        await self.bot.wait_until_ready()

    async def _nudge(self, key: tuple[int, int], stayed: float) -> None:
        enabled = await self.bot.db.scalar("SELECT nudges FROM user_prefs WHERE user_id=?", (key[1],))
        if enabled == 0:
            return
        guild = self.bot.get_guild(key[0])
        member = guild.get_member(key[1]) if guild else None
        if member is None:
            return
        try:
            await member.send(
                f"👋 You've been in voice on **{guild.name}** for {utils.fmt_duration(stayed)}. "
                "Stand up, stretch, drink some water, rest your eyes for a minute.\n"
                "Don't want these? Run `/nudges off` in the server."
            )
        except discord.HTTPException:
            pass

    # ---- commands --------------------------------------------------------
    @app_commands.command(name="stats", description="See study/focus stats, streak and level")
    @app_commands.guild_only()
    @app_commands.describe(member="Whose stats to show (default: you)")
    async def stats(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        member = member or interaction.user  # type: ignore[assignment]
        await interaction.response.defer()
        data = await self.collect_stats(interaction.guild_id, member)
        card = await self.stats_card_file(member, data)
        if card is not None:
            await interaction.followup.send(file=card)
        else:
            await interaction.followup.send(embed=self.stats_embed(member, data))

    async def collect_stats(self, guild_id: int, member: discord.Member) -> dict:
        """Everything the stats card / embed needs, in one place (also used by the button menu)."""
        today = utils.today_local()
        db = self.bot.db
        totals = await db.user_totals(guild_id, member.id, today)
        streak, best = await db.streaks(guild_id, member.id, today)
        return {
            "today": today,
            "totals": totals,
            "streak": streak,
            "best": best,
            "sessions": await db.focus_session_count(guild_id, member.id),
            "xp": await db.get_xp(guild_id, member.id),
            "daily": await db.daily_seconds(guild_id, member.id, (today - timedelta(days=7 * 26)).isoformat()),
        }

    async def stats_card_file(self, member: discord.Member, data: dict) -> discord.File | None:
        """Render the image card. Returns None (so callers fall back to text) if anything goes wrong."""
        try:
            import cardgen  # imported lazily so a Pillow problem can never stop the bot from starting

            avatar = None
            try:
                avatar = await member.display_avatar.replace(size=128, format="png").read()
            except Exception:
                pass
            level, title, into, needed, _ = leveling.progress(data["xp"])
            totals = data["totals"]
            png = await asyncio.to_thread(
                cardgen.render_stats_card,
                name=member.display_name, avatar_bytes=avatar, level=level, title=title,
                xp_into=into, xp_needed=needed, xp_total=data["xp"],
                today_seconds=totals["today"], week_seconds=totals["week"], total_seconds=totals["total"],
                streak=data["streak"], best_streak=data["best"], daily=data["daily"], today=data["today"],
            )
            return discord.File(io.BytesIO(png), filename="stats.png")
        except Exception:
            log.exception("Could not render stats card")
            return None

    def stats_embed(self, member: discord.Member, data: dict) -> discord.Embed:
        totals, streak, best, sessions = data["totals"], data["streak"], data["best"], data["sessions"]
        level, title, into, needed, frac = leveling.progress(data["xp"])
        embed = discord.Embed(title=f"📊 {member.display_name}'s stats", color=utils.BRAND)
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="Today", value=utils.fmt_duration(totals["today"]))
        embed.add_field(name="Last 7 days", value=utils.fmt_duration(totals["week"]))
        embed.add_field(name="This month", value=utils.fmt_duration(totals["month"]))
        embed.add_field(name="All time", value=utils.fmt_duration(totals["total"]))
        embed.add_field(name="Best day", value=utils.fmt_duration(totals["best_day"]))
        embed.add_field(name="Focus sessions", value=str(sessions))
        embed.add_field(name="🔥 Streak", value=f"{streak} day(s)  ·  best {best}")
        embed.add_field(
            name="Level",
            value=f"Level {level} · **{title}**\n{utils.progress_bar(frac)} {into:.0f} / {needed} XP",
            inline=False,
        )
        badges = badges_for(totals["total"] / 3600, streak, best, sessions)
        embed.add_field(name="Badges", value="  ".join(badges) if badges else "None yet - keep going!", inline=False)
        embed.set_footer(text="A streak day = at least 5 minutes of study/focus time.")
        return embed

    @app_commands.command(name="leaderboard", description="Top studiers in this server")
    @app_commands.guild_only()
    @app_commands.choices(
        period=[
            app_commands.Choice(name="Today", value="daily"),
            app_commands.Choice(name="Last 7 days", value="weekly"),
            app_commands.Choice(name="This month", value="monthly"),
            app_commands.Choice(name="All time", value="all"),
        ]
    )
    async def leaderboard(
        self, interaction: discord.Interaction, period: app_commands.Choice[str] | None = None
    ) -> None:
        value = period.value if period else "weekly"
        label = period.name if period else "Last 7 days"
        today = utils.today_local()
        since = {
            "daily": today.isoformat(),
            "weekly": (today - timedelta(days=6)).isoformat(),
            "monthly": today.replace(day=1).isoformat(),
            "all": "0000-00-00",
        }[value]
        rows = await self.bot.db.leaderboard(interaction.guild_id, since, 10)
        if not rows:
            await interaction.response.send_message("Nobody has logged time for this period yet. Be the first! 🏁")
            return
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            member = interaction.guild.get_member(row["user_id"]) if interaction.guild else None
            name = member.display_name if member else f"User {row['user_id']}"
            prefix = medals[i] if i < 3 else f"`{i + 1}.`"
            lines.append(f"{prefix} **{name}** - {utils.fmt_duration(row['total'])}")
        embed = discord.Embed(title=f"🏆 Leaderboard · {label}", description="\n".join(lines), color=utils.ORANGE)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="nudges", description="Turn break-reminder DMs on or off")
    @app_commands.choices(setting=[app_commands.Choice(name="on", value="on"), app_commands.Choice(name="off", value="off")])
    async def nudges(self, interaction: discord.Interaction, setting: app_commands.Choice[str]) -> None:
        enabled = 1 if setting.value == "on" else 0
        await self.bot.db.execute(
            "INSERT INTO user_prefs (user_id, nudges) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET nudges = excluded.nudges",
            (interaction.user.id, enabled),
        )
        text = "You'll get a gentle break reminder after long voice sessions." if enabled else "Break reminders are off."
        await interaction.response.send_message(text, ephemeral=True)

    # ---- weekly recap ----------------------------------------------------
    @tasks.loop(minutes=1)
    async def recap_loop(self) -> None:
        now = utils.now_local()
        if now.weekday() != config.RECAP_WEEKDAY or now.hour != config.RECAP_HOUR:
            return
        iso = now.isocalendar()
        week_key = f"{iso.year}-W{iso.week}"
        for guild in self.bot.guilds:
            settings = await self.bot.db.get_settings(guild.id)
            channel_id = settings["recap_channel"]
            if not channel_id:
                continue
            if not await self.bot.db.claim(f"recap:{guild.id}", week_key):
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            try:
                await self._post_recap(guild, channel)
            except discord.HTTPException:
                log.warning("Could not post weekly recap in guild %s", guild.id)

    @recap_loop.before_loop
    async def _before_recap(self) -> None:
        await self.bot.wait_until_ready()

    async def _post_recap(self, guild: discord.Guild, channel) -> None:
        since = (utils.today_local() - timedelta(days=6)).isoformat()
        rows = await self.bot.db.leaderboard(guild.id, since, 5)
        if not rows:
            await channel.send("📅 **Weekly recap** - quiet week! Let's make next week count. 💪")
            return
        medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]
        lines = []
        for i, row in enumerate(rows):
            member = guild.get_member(row["user_id"])
            name = member.mention if member else f"User {row['user_id']}"
            lines.append(f"{medals[i]} {name} - {utils.fmt_duration(row['total'])}")
        total_all = await self.bot.db.scalar(
            "SELECT COALESCE(SUM(seconds),0) FROM study_time WHERE guild_id=? AND day>=?", (guild.id, since)
        )
        people = await self.bot.db.scalar(
            "SELECT COUNT(DISTINCT user_id) FROM study_time WHERE guild_id=? AND day>=? AND seconds>0", (guild.id, since)
        )
        embed = discord.Embed(
            title="📅 Weekly recap - top focusers",
            description="\n".join(lines),
            color=utils.PURPLE,
        )
        embed.set_footer(text=f"{people} people logged {utils.fmt_duration(total_all or 0)} together this week. Keep it up!")
        await channel.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tracking(bot))
