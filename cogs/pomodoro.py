"""24/7 Pomodoro voice rooms: a shared focus/break cycle that never stops."""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import utils

log = logging.getLogger(__name__)


class Pomodoro(commands.Cog):
    pomodoro = app_commands.Group(
        name="pomodoro", description="24/7 Pomodoro study rooms", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # channel_id -> (cycle_index, phase, phase_end_ts); remembers what was last announced
        self.state: dict[int, tuple[int, str, int]] = {}

    async def cog_load(self) -> None:
        self.tick.start()

    async def cog_unload(self) -> None:
        self.tick.cancel()

    # ---- cycle maths -----------------------------------------------------
    @staticmethod
    def phase_at(now: float, focus_min: int, break_min: int) -> tuple[int, str, int]:
        """The cycle is aligned to the clock, so every room with the same lengths stays in sync."""
        cycle = (focus_min + break_min) * 60
        idx = int(now // cycle)
        pos = now - idx * cycle
        if pos < focus_min * 60:
            return idx, "focus", int(idx * cycle + focus_min * 60)
        return idx, "break", int((idx + 1) * cycle)

    @staticmethod
    def phase_embed(phase: str, end_ts: int, focus_min: int, break_min: int) -> discord.Embed:
        if phase == "focus":
            return discord.Embed(
                title=f"🍅 Focus time · {focus_min} min",
                description=f"Heads down! Focus ends <t:{end_ts}:R> (<t:{end_ts}:t>).\nMute notifications and work on one thing.",
                color=utils.GREEN,
            )
        return discord.Embed(
            title=f"☕ Break · {break_min} min",
            description=f"Stretch, drink water, rest your eyes. Next focus round starts <t:{end_ts}:R>.",
            color=utils.ORANGE,
        )

    # ---- loop ------------------------------------------------------------
    @tasks.loop(seconds=15)
    async def tick(self) -> None:
        now = time.time()
        for row in await self.bot.db.fetchall("SELECT * FROM pomodoro_rooms"):
            channel = self.bot.get_channel(row["channel_id"])
            if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)):
                continue
            idx, phase, end_ts = self.phase_at(now, row["focus_min"], row["break_min"])
            if self.state.get(channel.id, (None, None, 0))[:2] == (idx, phase):
                continue
            self.state[channel.id] = (idx, phase, end_ts)
            if not [m for m in channel.members if not m.bot]:
                continue  # nobody there: stay quiet, but keep the cycle moving
            try:
                await channel.send(embed=self.phase_embed(phase, end_ts, row["focus_min"], row["break_min"]))
            except discord.HTTPException:
                log.info("Could not post Pomodoro update in channel %s", channel.id)

    @tick.before_loop
    async def _before_tick(self) -> None:
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        """Tell someone who joins mid-cycle what phase the room is in."""
        if member.bot or after.channel is None or after.channel == before.channel:
            return
        channel = after.channel
        row = await self.bot.db.fetchone("SELECT * FROM pomodoro_rooms WHERE channel_id=?", (channel.id,))
        if row is None:
            return
        _, phase, end_ts = self.phase_at(time.time(), row["focus_min"], row["break_min"])
        label = "focus time 🍅" if phase == "focus" else "break time ☕"
        try:
            await channel.send(f"👋 {member.mention} joined - it's **{label}**, changing <t:{end_ts}:R>.", delete_after=30)
        except discord.HTTPException:
            pass

    # ---- admin commands --------------------------------------------------
    async def add_room(self, channel: discord.VoiceChannel, focus_min: int, break_min: int) -> None:
        await self.bot.db.execute(
            "INSERT INTO pomodoro_rooms (channel_id, guild_id, focus_min, break_min) VALUES (?,?,?,?) "
            "ON CONFLICT(channel_id) DO UPDATE SET focus_min = excluded.focus_min, break_min = excluded.break_min",
            (channel.id, channel.guild.id, focus_min, break_min),
        )
        self.state.pop(channel.id, None)

    @pomodoro.command(name="add", description="Turn a voice channel into a 24/7 Pomodoro room")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.describe(channel="The voice channel", focus_minutes="Focus length", break_minutes="Break length")
    async def pomodoro_add(
        self,
        interaction: discord.Interaction,
        channel: discord.VoiceChannel,
        focus_minutes: app_commands.Range[int, 5, 120] = 25,
        break_minutes: app_commands.Range[int, 1, 60] = 5,
    ) -> None:
        await self.add_room(channel, focus_minutes, break_minutes)
        await interaction.response.send_message(
            f"🍅 {channel.mention} now runs a nonstop **{focus_minutes}/{break_minutes}** Pomodoro cycle. "
            "I post each phase in that channel's text chat when people are inside.", ephemeral=True)

    @pomodoro.command(name="remove", description="Stop the Pomodoro cycle in a voice channel")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def pomodoro_remove(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
        res = await self.bot.db.execute("DELETE FROM pomodoro_rooms WHERE channel_id=?", (channel.id,))
        self.state.pop(channel.id, None)
        await interaction.response.send_message("Removed." if res.rowcount else "That channel isn't a Pomodoro room.", ephemeral=True)

    @pomodoro.command(name="list", description="List the Pomodoro rooms")
    async def pomodoro_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall("SELECT * FROM pomodoro_rooms WHERE guild_id=?", (interaction.guild_id,))
        if not rows:
            await interaction.response.send_message("No Pomodoro rooms yet. An admin can add one with `/pomodoro add`.", ephemeral=True)
            return
        lines = [f"<#{r['channel_id']}> - {r['focus_min']} min focus / {r['break_min']} min break" for r in rows]
        await interaction.response.send_message(embed=discord.Embed(
            title="🍅 Pomodoro rooms", description="\n".join(lines), color=utils.GREEN), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Pomodoro(bot))
