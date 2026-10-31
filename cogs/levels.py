"""XP, levels, level-up announcements, role rewards and the daily bonus."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

import discord
from discord import app_commands
from discord.ext import commands

import leveling
import utils

log = logging.getLogger(__name__)


class Levels(commands.Cog):
    xp = app_commands.Group(
        name="xp", description="Configure XP and level rewards", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- core ------------------------------------------------------------
    async def award(self, guild_id: int, user_id: int, amount: float, reason: str = "") -> None:
        new_total = await self.bot.db.add_xp(guild_id, user_id, amount)
        old_level = leveling.level_for_xp(new_total - amount)
        new_level = leveling.level_for_xp(new_total)
        if new_level > old_level:
            await self._level_up(guild_id, user_id, old_level, new_level)

    async def _announce_channel(self, guild: discord.Guild):
        db = self.bot.db
        channel_id = await db.get_config(guild.id, "levelup_channel")
        if not channel_id:
            channel_id = (await db.get_settings(guild.id)).get("checkin_channel")
        channel = guild.get_channel(int(channel_id)) if channel_id else None
        if channel is None or guild.me is None or not channel.permissions_for(guild.me).send_messages:
            return None
        return channel

    async def _level_up(self, guild_id: int, user_id: int, old_level: int, new_level: int) -> None:
        guild = self.bot.get_guild(guild_id)
        member = guild.get_member(user_id) if guild else None
        if guild is None or member is None or member.bot:
            return
        title = leveling.title_for_level(new_level)
        new_title = title != leveling.title_for_level(old_level)

        channel = await self._announce_channel(guild)
        if channel is not None:
            text = f"{member.mention} reached **level {new_level}**"
            text += f" and earned the title **{title}**! 🏅" if new_title else "! Keep it going."
            embed = discord.Embed(title="⬆️ Level up!", description=text, color=utils.GREEN)
            embed.set_thumbnail(url=member.display_avatar.url)
            try:
                await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(users=[member]))
            except discord.HTTPException:
                pass

        if new_title and await self.bot.db.get_config(guild.id, "level_roles", "0") == "1":
            await self._sync_role(member, title)

    async def _sync_role(self, member: discord.Member, title: str) -> None:
        guild, me = member.guild, member.guild.me
        if me is None or not me.guild_permissions.manage_roles:
            return
        role = discord.utils.get(guild.roles, name=title)
        if role is None:
            try:
                role = await guild.create_role(name=title, reason="Level reward role")
            except discord.HTTPException:
                return
        if role >= me.top_role:
            return
        stale = [r for r in member.roles if r.name in leveling.TITLE_NAMES and r.name != title]
        try:
            if stale:
                await member.remove_roles(*stale, reason="Level title changed")
            if role not in member.roles:
                await member.add_roles(role, reason="Level reward")
        except discord.HTTPException:
            log.info("Could not update level role for %s", member)

    # ---- daily bonus -----------------------------------------------------
    async def claim_daily(self, interaction: discord.Interaction) -> None:
        gid, uid = interaction.guild_id, interaction.user.id
        if gid is None:
            await interaction.response.send_message("Use this inside a server.", ephemeral=True)
            return
        today = utils.today_local()
        if not await self.bot.db.claim_daily(gid, uid, today.isoformat()):
            tomorrow = datetime.combine(today + timedelta(days=1), datetime.min.time(), tzinfo=utils.now_local().tzinfo)
            await interaction.response.send_message(
                f"🎁 You already claimed today's bonus. Come back <t:{int(tomorrow.timestamp())}:R>!", ephemeral=True
            )
            return
        streak = await self.bot.db.daily_claim_streak(gid, uid, today)
        bonus = leveling.daily_bonus(streak)
        total = await self.bot.db.get_xp(gid, uid) + bonus
        level, title, into, needed, frac = leveling.progress(total)
        embed = discord.Embed(
            title="🎁 Daily bonus claimed!",
            description=(
                f"**+{bonus} XP**  ·  🔥 {streak}-day claim streak"
                f"{' (max bonus!)' if streak >= leveling.XP_DAILY_MAX_STREAK else ''}\n\n"
                f"Level {level} · **{title}**\n{utils.progress_bar(frac)} {into:.0f} / {needed} XP"
            ),
            color=utils.ORANGE,
        )
        embed.set_footer(text="Claim every day to grow the bonus.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        await self.award(gid, uid, bonus, "daily")

    @app_commands.command(name="daily", description="Claim your daily XP bonus")
    @app_commands.guild_only()
    async def daily(self, interaction: discord.Interaction) -> None:
        await self.claim_daily(interaction)

    # ---- info ------------------------------------------------------------
    async def levels_embed(self, guild: discord.Guild) -> discord.Embed:
        rows = await self.bot.db.top_xp(guild.id, 10)
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            member = guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            lvl = leveling.level_for_xp(row["xp"])
            prefix = medals[i] if i < 3 else f"`{i + 1}.`"
            lines.append(f"{prefix} **{name}** - level {lvl} · {row['xp']:,.0f} XP")
        embed = discord.Embed(title="⭐ XP leaderboard", description="\n".join(lines) or "No XP earned yet - start a focus session!", color=utils.PURPLE)
        ladder = "\n".join(f"Level **{lvl}+** → {name}" for lvl, name in leveling.TITLES)
        embed.add_field(name="Title ladder", value=ladder, inline=False)
        embed.add_field(
            name="How to earn XP",
            value=(
                "• 1 XP per minute in voice or focus sessions\n"
                f"• +{leveling.XP_FOCUS_COMPLETE} XP for finishing a focus session\n"
                f"• +{leveling.XP_GOAL_DONE} XP for completing your daily goal (once a day)\n"
                f"• Daily bonus ({leveling.XP_DAILY_BASE}+ XP) with a streak boost\n"
                f"• +{leveling.XP_QUIZ_CORRECT} XP per correct quiz answer"
            ),
            inline=False,
        )
        return embed

    @app_commands.command(name="levels", description="XP leaderboard, title ladder and how to earn XP")
    @app_commands.guild_only()
    async def levels(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=await self.levels_embed(interaction.guild))

    # ---- admin config ----------------------------------------------------
    @xp.command(name="channel", description="Where level-up announcements are posted")
    @app_commands.checks.has_permissions(manage_guild=True)
    async def xp_channel(self, interaction: discord.Interaction, channel: discord.TextChannel) -> None:
        await self.bot.db.set_config(interaction.guild_id, "levelup_channel", channel.id)
        await interaction.response.send_message(f"✅ Level-ups will be announced in {channel.mention}.", ephemeral=True)

    @xp.command(name="roles", description="Give members a role for each title they earn")
    @app_commands.checks.has_permissions(manage_guild=True)
    @app_commands.choices(mode=[app_commands.Choice(name="on", value="1"), app_commands.Choice(name="off", value="0")])
    async def xp_roles(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        await self.bot.db.set_config(interaction.guild_id, "level_roles", mode.value)
        if mode.value == "1":
            text = (
                "✅ Title roles are **on**. When someone reaches a new title I create the role if needed and assign it. "
                "Make sure my role is **above** them in Server Settings → Roles."
            )
        else:
            text = "Title roles are **off**."
        await interaction.response.send_message(text, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Levels(bot))
