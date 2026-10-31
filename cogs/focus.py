"""Pomodoro and group ("body doubling") focus sessions."""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

import config
import leveling
import utils

log = logging.getLogger(__name__)

FOCUS_ROLE_NAME = "Focusing"  # optional: create a role with this name and the bot will hand it out


class FocusSession:
    def __init__(self, guild_id: int, channel, host_id: int, minutes: int, break_minutes: int):
        self.guild_id = guild_id
        self.channel = channel
        self.host_id = host_id
        self.minutes = minutes
        self.break_minutes = break_minutes
        self.started = time.time()
        self.ends_at = self.started + minutes * 60
        self.break_ends_at = 0.0
        self.participants: dict[int, float] = {host_id: self.started}  # user_id -> join timestamp
        self.phase = "focus"  # focus | break | done
        self.message: discord.PartialMessage | None = None
        self.task: asyncio.Task | None = None

    def embed(self) -> discord.Embed:
        people = " ".join(f"<@{uid}>" for uid in list(self.participants)[:25]) or "-"
        if self.phase == "focus":
            embed = discord.Embed(
                title=f"🎯 Focus session · {self.minutes} min",
                description=(
                    f"Ends <t:{int(self.ends_at)}:R> (<t:{int(self.ends_at)}:t>)\n"
                    f"Host: <@{self.host_id}>\n\n"
                    "Phone down, notifications off. Press **Join** to focus together."
                ),
                color=utils.GREEN,
            )
        elif self.phase == "break":
            embed = discord.Embed(
                title="☕ Break time",
                description=f"Back to it <t:{int(self.break_ends_at)}:R>. Stretch, hydrate, rest your eyes.",
                color=utils.ORANGE,
            )
        else:
            embed = discord.Embed(
                title="✅ Session finished",
                description="Nice work. Start another with `/focus start`.",
                color=utils.BRAND,
            )
        embed.add_field(name=f"Focusing ({len(self.participants)})", value=people, inline=False)
        return embed


class SessionView(discord.ui.View):
    def __init__(self, cog: "Focus", session: FocusSession):
        super().__init__(timeout=None)
        self.cog = cog
        self.session = session

    @discord.ui.button(label="Join", style=discord.ButtonStyle.success, emoji="🤝")
    async def join(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        s = self.session
        uid = interaction.user.id
        if s.phase != "focus":
            await interaction.response.send_message("This session is no longer in its focus phase.", ephemeral=True)
            return
        if uid in s.participants:
            await interaction.response.send_message("You're already in this session.", ephemeral=True)
            return
        other = self.cog.find_session(s.guild_id, uid)
        if other is not None:
            await interaction.response.send_message(
                f"You're already focusing in <#{other.channel.id}>. Leave that one first.", ephemeral=True
            )
            return
        s.participants[uid] = time.time()
        await self.cog.toggle_focus_role(interaction.guild, [uid], add=True)
        await interaction.response.edit_message(embed=s.embed())
        await interaction.followup.send("You're in. Let's focus! 🔥", ephemeral=True)

    @discord.ui.button(label="Leave", style=discord.ButtonStyle.secondary)
    async def leave(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        s = self.session
        uid = interaction.user.id
        if uid not in s.participants:
            await interaction.response.send_message("You're not in this session.", ephemeral=True)
            return
        if uid == s.host_id:
            await interaction.response.send_message("You're the host - use `/focus stop` to end the session.", ephemeral=True)
            return
        if s.phase == "focus":
            await self.cog.credit(s, {uid: s.participants[uid]}, time.time())
        s.participants.pop(uid, None)
        await self.cog.toggle_focus_role(interaction.guild, [uid], add=False)
        await interaction.response.edit_message(embed=s.embed())
        await interaction.followup.send("You left the session. Come back anytime!", ephemeral=True)


class Focus(commands.Cog):
    focus = app_commands.Group(name="focus", description="Pomodoro and group focus sessions", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.sessions: dict[int, FocusSession] = {}  # channel_id -> session

    async def cog_unload(self) -> None:
        for s in list(self.sessions.values()):
            if s.task:
                s.task.cancel()
            guild = self.bot.get_guild(s.guild_id)
            if guild:
                await self.toggle_focus_role(guild, list(s.participants), add=False)
        self.sessions.clear()

    # ---- helpers ---------------------------------------------------------
    def find_session(self, guild_id: int, user_id: int) -> FocusSession | None:
        for s in self.sessions.values():
            if s.guild_id == guild_id and s.phase == "focus" and user_id in s.participants:
                return s
        return None

    async def toggle_focus_role(self, guild: discord.Guild | None, user_ids: list[int], add: bool) -> None:
        if guild is None:
            return
        role = discord.utils.get(guild.roles, name=FOCUS_ROLE_NAME)
        me = guild.me
        if role is None or me is None or not me.guild_permissions.manage_roles or role >= me.top_role:
            return
        for uid in user_ids:
            member = guild.get_member(uid)
            if member is None:
                continue
            try:
                if add:
                    await member.add_roles(role, reason="Focus session started")
                else:
                    await member.remove_roles(role, reason="Focus session ended")
            except discord.HTTPException:
                pass

    async def credit(self, s: FocusSession, who: dict[int, float], until: float) -> None:
        """Add focused time to each participant. Time spent in a tracked voice channel
        is already counted by the Tracking cog, so only the focus-only bucket is bumped then."""
        tracking = self.bot.get_cog("Tracking")
        guild = self.bot.get_guild(s.guild_id)
        day = datetime.fromtimestamp(until, config.TZ).date().isoformat()
        for uid, joined in who.items():
            seconds = int(until - joined)
            if seconds < 60:
                continue
            member = guild.get_member(uid) if guild else None
            in_voice = bool(member and tracking and await tracking.is_tracked_voice(member))
            await self.bot.db.add_study(s.guild_id, uid, day, 0 if in_voice else seconds, seconds)
            if not in_voice:  # voice time already earns XP through the Tracking cog
                await leveling.grant(self.bot, s.guild_id, uid, seconds / 60, "focus")

    # ---- session lifecycle -----------------------------------------------
    async def _run(self, s: FocusSession) -> None:
        try:
            await asyncio.sleep(max(0.0, s.ends_at - time.time()))
            await self._finish_focus(s)
            if s.break_minutes > 0:
                s.phase = "break"
                s.break_ends_at = time.time() + s.break_minutes * 60
                await self._edit(s, view=None)
                await asyncio.sleep(s.break_minutes * 60)
            await self._close(
                s,
                "☕ Break's over - ready for another round? `/focus start`"
                if s.break_minutes
                else "Ready for another round? `/focus start`",
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Focus session crashed")
            self.sessions.pop(s.channel.id, None)

    async def _finish_focus(self, s: FocusSession) -> None:
        guild = self.bot.get_guild(s.guild_id)
        await self.credit(s, dict(s.participants), s.ends_at)
        completed_at = utils.now_local().isoformat(timespec="seconds")
        for uid, joined in s.participants.items():
            minutes = int((s.ends_at - joined) / 60)
            if minutes >= 1:
                await self.bot.db.record_focus(s.guild_id, uid, minutes, completed_at)
                await leveling.grant(self.bot, s.guild_id, uid, leveling.XP_FOCUS_COMPLETE, "focus complete")
        await self.toggle_focus_role(guild, list(s.participants), add=False)
        mentions = " ".join(f"<@{uid}>" for uid in list(s.participants)[:25])
        extra = f" Take a {s.break_minutes}-minute break." if s.break_minutes else ""
        try:
            await s.channel.send(f"⏰ **Focus complete!** {mentions}\nGreat work - {s.minutes} minutes done.{extra}")
        except discord.HTTPException:
            pass

    async def _close(self, s: FocusSession, text: str) -> None:
        s.phase = "done"
        await self._edit(s, view=None)
        try:
            await s.channel.send(text)
        except discord.HTTPException:
            pass
        self.sessions.pop(s.channel.id, None)

    async def _edit(self, s: FocusSession, view) -> None:
        if s.message is None:
            return
        try:
            await s.message.edit(embed=s.embed(), view=view)
        except discord.HTTPException:
            pass

    # ---- commands --------------------------------------------------------
    @focus.command(name="start", description="Start a focus session that others can join")
    @app_commands.describe(minutes="Focus length in minutes (1-180)", break_minutes="Break length afterwards (0-60)")
    async def focus_start(
        self,
        interaction: discord.Interaction,
        minutes: app_commands.Range[int, 1, 180] = 25,
        break_minutes: app_commands.Range[int, 0, 60] = 5,
    ) -> None:
        await self.begin(interaction, minutes, break_minutes)

    async def begin(self, interaction: discord.Interaction, minutes: int, break_minutes: int) -> None:
        """Start a session in the current channel. Used by /focus start and by the button menu."""
        channel = interaction.channel
        if channel is None or interaction.guild is None:
            await interaction.response.send_message("Use this in a server channel.", ephemeral=True)
            return
        if channel.id in self.sessions:
            await interaction.response.send_message("A session is already running in this channel - join it!", ephemeral=True)
            return
        existing = self.find_session(interaction.guild.id, interaction.user.id)
        if existing is not None:
            await interaction.response.send_message(
                f"You're already in a session in <#{existing.channel.id}>.", ephemeral=True
            )
            return

        s = FocusSession(interaction.guild.id, channel, interaction.user.id, minutes, break_minutes)
        self.sessions[channel.id] = s
        try:
            # Posted with channel.send (not the interaction response) so it is public even when
            # the click came from a private menu.
            message = await channel.send(embed=s.embed(), view=SessionView(self, s))
        except discord.HTTPException:
            self.sessions.pop(channel.id, None)
            await interaction.response.send_message(
                "I can't post in this channel - check my Send Messages and Embed Links permissions.", ephemeral=True
            )
            return
        s.message = channel.get_partial_message(message.id)
        await interaction.response.send_message(f"🎯 Session started in <#{channel.id}>. You're in - good luck!", ephemeral=True)
        await self.toggle_focus_role(interaction.guild, [interaction.user.id], add=True)
        s.task = asyncio.create_task(self._run(s))

    @focus.command(name="stop", description="End the focus session in this channel")
    async def focus_stop(self, interaction: discord.Interaction) -> None:
        s = self.sessions.get(interaction.channel_id or 0)
        if s is None:
            await interaction.response.send_message("There's no session running in this channel.", ephemeral=True)
            return
        perms = interaction.permissions
        if interaction.user.id != s.host_id and not perms.manage_messages:
            await interaction.response.send_message("Only the host or a moderator can stop this session.", ephemeral=True)
            return
        if s.task:
            s.task.cancel()
        if s.phase == "focus":
            await self.credit(s, dict(s.participants), time.time())
        await self.toggle_focus_role(interaction.guild, list(s.participants), add=False)
        s.phase = "done"
        await interaction.response.send_message("🛑 Session stopped. Time you already put in has been saved.")
        await self._edit(s, view=None)
        self.sessions.pop(s.channel.id, None)

    @focus.command(name="status", description="Show the focus session in this channel")
    async def focus_status(self, interaction: discord.Interaction) -> None:
        s = self.sessions.get(interaction.channel_id or 0)
        if s is None:
            await interaction.response.send_message("No session here. Start one with `/focus start`.", ephemeral=True)
            return
        await interaction.response.send_message(embed=s.embed(), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Focus(bot))
