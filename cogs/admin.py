"""Server setup, welcome flow, self-assign roles, basic auto-moderation and /help."""
from __future__ import annotations

import logging
import re
import time
from collections import defaultdict, deque
from datetime import timedelta

import discord
from discord import app_commands
from discord.ext import commands

import db as dbmod
import utils

log = logging.getLogger(__name__)

SKILL_ROLES = ["Beginner", "Intermediate", "Pro"]
INTEREST_ROLES = ["Web Dev", "AI/ML", "Cybersecurity", "Mobile", "DevOps", "Game Dev", "DSA/CP", "Data Science"]

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.I)
INVITE_RE = re.compile(r"(?:discord\.gg|discord(?:app)?\.com/invite)/\S+", re.I)
SPAM_COUNT = 6      # messages ...
SPAM_WINDOW = 6.0   # ... within this many seconds trigger the spam filter
NEW_MEMBER_AGE = timedelta(hours=24)

KIND_CHOICES = [
    app_commands.Choice(name="Daily goal check-in", value="checkin"),
    app_commands.Choice(name="Weekly recap", value="recap"),
    app_commands.Choice(name="Tech news", value="news"),
    app_commands.Choice(name="Daily coding challenge", value="challenge"),
    app_commands.Choice(name="Daily 'what are you working on' thread", value="daily"),
    app_commands.Choice(name="Welcome messages", value="welcome"),
]

admin_only = app_commands.checks.has_permissions(manage_guild=True)


# ---------------------------------------------------------------------------
# Self-assign role panel (persistent)
# ---------------------------------------------------------------------------
async def apply_roles(interaction: discord.Interaction, chosen: list[str], group: list[str]) -> None:
    guild, member = interaction.guild, interaction.user
    await interaction.response.defer(ephemeral=True)
    if guild is None or not isinstance(member, discord.Member):
        await interaction.followup.send("This only works in a server.", ephemeral=True)
        return
    to_add: list[discord.Role] = []
    to_remove: list[discord.Role] = []
    for name in group:
        role = discord.utils.get(guild.roles, name=name)
        if name in chosen:
            if role is None:
                try:
                    role = await guild.create_role(name=name, reason="Self-assign role")
                except discord.HTTPException:
                    continue
            if role not in member.roles:
                to_add.append(role)
        elif role is not None and role in member.roles:
            to_remove.append(role)
    try:
        if to_add:
            await member.add_roles(*to_add, reason="Role panel")
        if to_remove:
            await member.remove_roles(*to_remove, reason="Role panel")
    except discord.Forbidden:
        await interaction.followup.send(
            "I can't manage those roles yet. Ask an admin to move my role above them in Server Settings → Roles.",
            ephemeral=True,
        )
        return
    await interaction.followup.send("✅ Your roles are updated.", ephemeral=True)


class SkillSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            custom_id="roles:skill", placeholder="Pick your skill level", min_values=1, max_values=1,
            options=[discord.SelectOption(label=n, value=n) for n in SKILL_ROLES],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await apply_roles(interaction, list(self.values), SKILL_ROLES)


class InterestSelect(discord.ui.Select):
    def __init__(self) -> None:
        super().__init__(
            custom_id="roles:interests", placeholder="Pick your interests (replaces previous picks)",
            min_values=0, max_values=len(INTEREST_ROLES),
            options=[discord.SelectOption(label=n, value=n) for n in INTEREST_ROLES],
        )

    async def callback(self, interaction: discord.Interaction) -> None:
        await apply_roles(interaction, list(self.values), INTEREST_ROLES)


class RolesView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)
        self.add_item(SkillSelect())
        self.add_item(InterestSelect())


# ---------------------------------------------------------------------------
# Welcome flow (persistent button -> modal)
# ---------------------------------------------------------------------------
class GoalModal(discord.ui.Modal, title="Your first goal"):
    goal = discord.ui.TextInput(label="What do you want to achieve this week?", max_length=200)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        await db.set_goal(interaction.guild_id, interaction.user.id, utils.today_str(), self.goal.value)
        await interaction.response.send_message(
            f"🎯 Goal saved: **{self.goal.value}**\nStart working on it with `/focus start`!", ephemeral=True
        )


class WelcomeView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Set my first goal", style=discord.ButtonStyle.primary, emoji="🎯", custom_id="welcome:goal")
    async def set_goal(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(GoalModal())

    @discord.ui.button(label="Open the menu", style=discord.ButtonStyle.secondary, emoji="🧠", custom_id="welcome:menu")
    async def open_menu(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        panel = interaction.client.get_cog("Panel")
        if panel is None:
            await interaction.response.send_message("The menu isn't available right now.", ephemeral=True)
            return
        await panel.open_main(interaction)


# ---------------------------------------------------------------------------
class Admin(commands.Cog):
    setup = app_commands.Group(
        name="setup", description="Configure the bot for this server", guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )
    roles = app_commands.Group(
        name="roles", description="Self-assign role panel", guild_only=True,
        default_permissions=discord.Permissions(manage_roles=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._recent: dict[tuple[int, int], deque[tuple[float, discord.Message]]] = defaultdict(deque)

    async def cog_load(self) -> None:
        self.bot.add_view(RolesView())
        self.bot.add_view(WelcomeView())

    # ---- /setup ----------------------------------------------------------
    @setup.command(name="channel", description="Choose where a feature posts")
    @admin_only
    @app_commands.choices(kind=KIND_CHOICES)
    async def setup_channel(self, interaction: discord.Interaction, kind: app_commands.Choice[str], channel: discord.TextChannel) -> None:
        await self.bot.db.set_setting(interaction.guild_id, dbmod.CHANNEL_KINDS[kind.value], channel.id)
        await interaction.response.send_message(f"✅ **{kind.name}** will use {channel.mention}.", ephemeral=True)

    @setup.command(name="lobby", description="Set the 'join to create a study room' voice channel")
    @admin_only
    async def setup_lobby(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
        await self.bot.db.set_setting(interaction.guild_id, "lobby_channel", channel.id)
        await interaction.response.send_message(
            f"✅ Joining **{channel.name}** now creates a private study room. "
            "(I need Manage Channels and Move Members permissions.)", ephemeral=True)

    @setup.command(name="track_add", description="Only count study time in specific voice channels (call per channel)")
    @admin_only
    async def setup_track_add(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
        await self.bot.db.execute(
            "INSERT OR IGNORE INTO tracked_channels (guild_id, channel_id) VALUES (?,?)", (interaction.guild_id, channel.id)
        )
        await interaction.response.send_message(f"✅ Study time is now tracked in {channel.mention}.", ephemeral=True)

    @setup.command(name="track_remove", description="Stop tracking a voice channel")
    @admin_only
    async def setup_track_remove(self, interaction: discord.Interaction, channel: discord.VoiceChannel) -> None:
        await self.bot.db.execute(
            "DELETE FROM tracked_channels WHERE guild_id=? AND channel_id=?", (interaction.guild_id, channel.id)
        )
        await interaction.response.send_message(f"Removed {channel.mention} from tracking.", ephemeral=True)

    @setup.command(name="track_clear", description="Track ALL voice channels again (default)")
    @admin_only
    async def setup_track_clear(self, interaction: discord.Interaction) -> None:
        await self.bot.db.execute("DELETE FROM tracked_channels WHERE guild_id=?", (interaction.guild_id,))
        await interaction.response.send_message("✅ Every voice channel (except AFK and the lobby) is tracked.", ephemeral=True)

    @setup.command(name="automod", description="Turn the spam and link filters on or off")
    @admin_only
    async def setup_automod(self, interaction: discord.Interaction, spam_filter: bool, link_filter: bool) -> None:
        await self.bot.db.set_setting(interaction.guild_id, "spam_filter", int(spam_filter))
        await self.bot.db.set_setting(interaction.guild_id, "link_filter", int(link_filter))
        await interaction.response.send_message(
            f"✅ Spam filter: **{'on' if spam_filter else 'off'}** · Link filter for new members: **{'on' if link_filter else 'off'}**",
            ephemeral=True)

    @setup.command(name="show", description="Show the current configuration")
    @admin_only
    async def setup_show(self, interaction: discord.Interaction) -> None:
        s = await self.bot.db.get_settings(interaction.guild_id)
        lines = []
        for kind, column in dbmod.CHANNEL_KINDS.items():
            lines.append(f"**{kind}**: {f'<#{s[column]}>' if s[column] else 'not set'}")
        lobby = f"<#{s['lobby_channel']}>" if s["lobby_channel"] else "not set"
        lines.append(f"**study-room lobby**: {lobby}")
        tracked = await self.bot.db.fetchall("SELECT channel_id FROM tracked_channels WHERE guild_id=?", (interaction.guild_id,))
        lines.append("**tracked voice channels**: " + (" ".join(f"<#{r['channel_id']}>" for r in tracked) or "all"))
        lines.append(f"**spam filter**: {'on' if s['spam_filter'] else 'off'} · **link filter**: {'on' if s['link_filter'] else 'off'}")
        await interaction.response.send_message(
            embed=discord.Embed(title="⚙️ Server setup", description="\n".join(lines), color=utils.BRAND), ephemeral=True)

    # ---- /roles ----------------------------------------------------------
    @roles.command(name="panel", description="Post the self-assign role panel in this channel")
    @app_commands.checks.has_permissions(manage_roles=True)
    async def roles_panel(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        for name in SKILL_ROLES + INTEREST_ROLES:
            if discord.utils.get(interaction.guild.roles, name=name) is None:
                try:
                    await interaction.guild.create_role(name=name, reason="Role panel setup")
                except discord.HTTPException:
                    await interaction.followup.send("I need the **Manage Roles** permission to create the roles.", ephemeral=True)
                    return
        embed = discord.Embed(
            title="🎭 Pick your roles",
            description="Choose your **skill level** and **interests** below so others know how to help you - "
                        "and so you get matched with the right people.",
            color=utils.PURPLE,
        )
        try:
            await interaction.channel.send(embed=embed, view=RolesView())
        except discord.HTTPException:
            await interaction.followup.send("I can't post in this channel.", ephemeral=True)
            return
        await interaction.followup.send("✅ Panel posted. Make sure my role sits above these roles in the role list.", ephemeral=True)

    # ---- welcome ---------------------------------------------------------
    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        if member.bot:
            return
        settings = await self.bot.db.get_settings(member.guild.id)
        channel = member.guild.get_channel(settings["welcome_channel"]) if settings["welcome_channel"] else member.guild.system_channel
        if channel is None:
            return
        embed = discord.Embed(
            title=f"👋 Welcome, {member.display_name}!",
            description=(
                "This server is all about getting things done together.\n\n"
                "• Pick your roles with the panel in the roles channel\n"
                "• Start a session with `/focus start`\n"
                "• Check your progress with `/stats` and `/leaderboard`\n"
                "• Stuck on code? Use `/explain` or `/review`\n\n"
                "Tell us what you're aiming for 👇"
            ),
            color=utils.GREEN,
        )
        try:
            await channel.send(member.mention, embed=embed, view=WelcomeView())
        except discord.HTTPException:
            log.info("Could not send welcome message in guild %s", member.guild.id)

    # ---- auto-moderation -------------------------------------------------
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot or message.guild is None or not isinstance(message.author, discord.Member):
            return
        member = message.author
        perms = member.guild_permissions
        if perms.administrator or perms.manage_messages:
            return
        settings = await self.bot.db.get_settings(message.guild.id)

        if settings["spam_filter"]:
            now = time.monotonic()
            window = self._recent[(message.guild.id, member.id)]
            window.append((now, message))
            while window and now - window[0][0] > SPAM_WINDOW:
                window.popleft()
            if len(window) >= SPAM_COUNT:
                offending = [m for _, m in window]
                window.clear()
                for m in offending:
                    try:
                        await m.delete()
                    except discord.HTTPException:
                        pass
                try:
                    await member.timeout(timedelta(minutes=5), reason="Automod: spam")
                    note = "You've been timed out for 5 minutes."
                except discord.HTTPException:
                    note = "Please slow down."
                await message.channel.send(f"{member.mention} that looked like spam. {note}", delete_after=10)
                return

        if settings["link_filter"]:
            if INVITE_RE.search(message.content):
                await self._remove_with_notice(message, "Server invite links aren't allowed here.")
                return
            joined = member.joined_at
            is_new = joined is not None and discord.utils.utcnow() - joined < NEW_MEMBER_AGE
            if is_new and URL_RE.search(message.content):
                await self._remove_with_notice(message, "New members can't post links for their first 24 hours.")

    async def _remove_with_notice(self, message: discord.Message, reason: str) -> None:
        try:
            await message.delete()
            await message.channel.send(f"{message.author.mention} {reason}", delete_after=8)
        except discord.HTTPException:
            pass

    # ---- /help -----------------------------------------------------------
    @app_commands.command(name="help", description="See everything this bot can do")
    async def help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(title="🧠 Productivity bot - commands", color=utils.BRAND)
        embed.add_field(name="🎯 Focus", value="`/focus start` · `/focus stop` · `/focus status`", inline=False)
        embed.add_field(name="📊 Progress", value="`/stats` · `/leaderboard` · `/nudges`", inline=False)
        embed.add_field(name="✅ Accountability", value="`/goal set|done|view` · `/todo add|list|done|remove|visibility` · `/partner join|status|leave`", inline=False)
        embed.add_field(
            name="💻 Tech",
            value="`/snippet save|get|list` · `/docs` · `/explain` · `/review` · `/challenge today` · `/news` · `/trending` · `/resources`",
            inline=False,
        )
        embed.add_field(name="🌿 Environment", value="`/remind add|list` · `/room lock|invite|rename|limit` · `/quiet on|off` · `/ambient` · `/poll`", inline=False)
        embed.add_field(
            name="🆕 More",
            value="`/daily` · `/levels` · `/quiz` · `/flashcards add|review|list` · `/run` · `/github` · `/pypi` · `/pomodoro list`",
            inline=False,
        )
        embed.add_field(
            name="⚙️ Admin",
            value="`/wizard` (one-click setup) · `/panel` · `/setup channel|lobby|track_add|automod|show` · `/roles panel` · `/pomodoro add` · `/xp channel|roles`",
            inline=False,
        )
        embed.description = "**Don't like typing commands?** Use **/menu** - everything is a button."
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
