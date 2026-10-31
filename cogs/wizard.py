"""/wizard - guided server setup. One click creates the channels, saves the settings and posts the panels."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands

import db as dbmod
import utils
from cogs.admin import INTEREST_ROLES, SKILL_ROLES, RolesView
from cogs.panel import SHORT, HubView, QuickModal, hub_embed

log = logging.getLogger(__name__)

CATEGORY_NAME = "🧠 Productivity"

# (channel name, topic, setup kind)
TEXT_CHANNELS = [
    ("hub", "Tap a button - no commands needed", None),
    ("welcome", "Welcome messages for new members", "welcome"),
    ("roles", "Pick your skill level and interests", None),
    ("daily-goals", "Evening goal check-ins", "checkin"),
    ("daily-threads", "What are you working on today?", "daily"),
    ("tech-news", "Daily tech headlines", "news"),
    ("daily-challenge", "A coding problem every day", "challenge"),
    ("weekly-recap", "Top focusers of the week", "recap"),
    ("level-ups", "Level-up celebrations", "levelup"),
]
# (channel name, kind, (focus, break))
VOICE_CHANNELS = [
    ("➕ Create a study room", "lobby", None),
    ("🍅 Pomodoro 25-5", "pomodoro", (25, 5)),
    ("🍅 Pomodoro 50-10", "pomodoro", (50, 10)),
]
MANUAL_KINDS = [
    ("checkin", "Daily goal check-in"), ("recap", "Weekly recap"), ("news", "Tech news"),
    ("challenge", "Daily coding challenge"), ("daily", "Daily 'what are you working on' thread"),
    ("welcome", "Welcome messages"), ("levelup", "Level-up announcements"),
]


class Wizard(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- status ----------------------------------------------------------
    async def status_embed(self, guild: discord.Guild) -> discord.Embed:
        db = self.bot.db
        settings = await db.get_settings(guild.id)
        lines = []
        for kind, column in dbmod.CHANNEL_KINDS.items():
            lines.append(f"**{kind}**: {f'<#{settings[column]}>' if settings[column] else '*not set*'}")
        levelup = await db.get_config(guild.id, "levelup_channel")
        lines.append(f"**level-ups**: {f'<#{levelup}>' if levelup else '*not set*'}")
        lines.append(f"**study-room lobby**: {f'<#{settings['lobby_channel']}>' if settings['lobby_channel'] else '*not set*'}")
        rooms = await db.fetchall("SELECT channel_id FROM pomodoro_rooms WHERE guild_id=?", (guild.id,))
        lines.append("**Pomodoro rooms**: " + (" ".join(f"<#{r['channel_id']}>" for r in rooms) or "*none*"))
        roles_on = await db.get_config(guild.id, "level_roles", "0") == "1"
        lines.append(f"**level title roles**: {'on' if roles_on else 'off'}")
        embed = discord.Embed(
            title="🧙 Setup wizard",
            description=(
                "**⚡ Auto-setup** creates a *🧠 Productivity* category with every channel the bot uses, "
                "saves the settings and posts the button panel and role menu. Safe to run again - it reuses what exists.\n\n"
                "Or use the other buttons to choose channels yourself.\n\n" + "\n".join(lines)
            ),
            color=utils.BRAND,
        )
        embed.set_footer(text="The bot needs Manage Channels and Manage Roles for auto-setup.")
        return embed

    # ---- command ---------------------------------------------------------
    @app_commands.command(name="wizard", description="Guided setup: create channels, panels and settings in one click")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def wizard(self, interaction: discord.Interaction) -> None:
        view = WizardView(self, interaction.user.id, await self._roles_on(interaction.guild_id))
        await interaction.response.send_message(embed=await self.status_embed(interaction.guild), view=view, ephemeral=True)

    async def _roles_on(self, guild_id: int) -> bool:
        return await self.bot.db.get_config(guild_id, "level_roles", "0") == "1"

    # ---- auto setup ------------------------------------------------------
    def _read_only(self, guild: discord.Guild) -> dict:
        return {
            guild.default_role: discord.PermissionOverwrite(
                send_messages=False, create_public_threads=False, send_messages_in_threads=True, add_reactions=True
            ),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True, attach_files=True,
                create_public_threads=True, send_messages_in_threads=True, manage_messages=True,
            ),
        }

    async def _already_posted(self, channel: discord.TextChannel, title_prefix: str) -> bool:
        try:
            async for message in channel.history(limit=20):
                if message.author.id == self.bot.user.id and message.embeds and (message.embeds[0].title or "").startswith(title_prefix):
                    return True
        except discord.HTTPException:
            pass
        return False

    async def post_roles_panel(self, guild: discord.Guild, channel: discord.TextChannel) -> bool:
        for name in SKILL_ROLES + INTEREST_ROLES:
            if discord.utils.get(guild.roles, name=name) is None:
                try:
                    await guild.create_role(name=name, reason="Role panel setup")
                except discord.HTTPException:
                    return False
        embed = discord.Embed(
            title="🎭 Pick your roles",
            description="Choose your **skill level** and **interests** below so others know how to help you.",
            color=utils.PURPLE,
        )
        try:
            await channel.send(embed=embed, view=RolesView())
        except discord.HTTPException:
            return False
        return True

    async def auto_setup(self, interaction: discord.Interaction) -> None:
        guild, db = interaction.guild, self.bot.db
        if not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "I need the **Manage Channels** permission to create channels. Give it to my role and run this again, "
                "or use **Choose channels manually**.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)

        created: list[str] = []
        reused: list[str] = []
        failed: list[str] = []

        category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
        if category is None:
            try:
                category = await guild.create_category(CATEGORY_NAME, reason="Productivity bot setup")
            except discord.HTTPException:
                category = None  # channels will simply be created at the top level

        text: dict[str, discord.TextChannel] = {}
        for name, topic, kind in TEXT_CHANNELS:
            channel = discord.utils.get(guild.text_channels, name=name)
            if channel is None:
                try:
                    channel = await guild.create_text_channel(
                        name, category=category, topic=topic, overwrites=self._read_only(guild),
                        reason="Productivity bot setup",
                    )
                    created.append(f"#{name}")
                except discord.HTTPException:
                    failed.append(f"#{name}")
                    continue
            else:
                reused.append(f"#{name}")
            text[name] = channel
            if kind == "levelup":
                await db.set_config(guild.id, "levelup_channel", channel.id)
            elif kind:
                await db.set_setting(guild.id, dbmod.CHANNEL_KINDS[kind], channel.id)

        pomodoro = self.bot.get_cog("Pomodoro")
        for name, kind, cycle in VOICE_CHANNELS:
            channel = discord.utils.get(guild.voice_channels, name=name)
            if channel is None:
                try:
                    channel = await guild.create_voice_channel(name, category=category, reason="Productivity bot setup")
                    created.append(name)
                except discord.HTTPException:
                    failed.append(name)
                    continue
            else:
                reused.append(name)
            if kind == "lobby":
                await db.set_setting(guild.id, "lobby_channel", channel.id)
            elif kind == "pomodoro" and pomodoro is not None and cycle:
                await pomodoro.add_room(channel, cycle[0], cycle[1])

        posted: list[str] = []
        hub = text.get("hub")
        if hub is not None and not await self._already_posted(hub, "🧠 Productivity Hub"):
            try:
                await hub.send(embed=hub_embed(), view=HubView())
                posted.append("button panel in " + hub.mention)
            except discord.HTTPException:
                failed.append("button panel")
        roles = text.get("roles")
        if roles is not None and not await self._already_posted(roles, "🎭 Pick your roles"):
            if await self.post_roles_panel(guild, roles):
                posted.append("role menu in " + roles.mention)
            else:
                failed.append("role menu (needs Manage Roles)")

        embed = discord.Embed(title="✅ Setup finished", color=utils.GREEN if not failed else utils.ORANGE)
        if created:
            embed.add_field(name="Created", value=", ".join(created), inline=False)
        if reused:
            embed.add_field(name="Already existed (reused)", value=", ".join(reused), inline=False)
        if posted:
            embed.add_field(name="Posted", value="\n".join(posted), inline=False)
        if failed:
            embed.add_field(name="⚠️ Couldn't do", value=", ".join(failed) + "\nCheck my permissions and run the wizard again.", inline=False)
        embed.add_field(
            name="Next steps",
            value=(
                "• Drag my role **above** the roles I should hand out (Server Settings → Roles)\n"
                "• Pin the panel in " + (hub.mention if hub else "#hub") + "\n"
                "• Optional: turn on **title roles** in `/wizard`"
            ),
            inline=False,
        )
        await interaction.followup.send(embed=embed, ephemeral=True)


# ---------------------------------------------------------------------------
class WizardView(discord.ui.View):
    def __init__(self, cog: Wizard, owner_id: int, roles_on: bool):
        super().__init__(timeout=600)
        self.cog, self.owner_id = cog, owner_id
        self.toggle_roles.label = "Title roles: ON" if roles_on else "Title roles: OFF"
        self.toggle_roles.style = discord.ButtonStyle.success if roles_on else discord.ButtonStyle.secondary

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not interaction.permissions.manage_guild:
            await interaction.response.send_message("Only the admin who opened the wizard can use these buttons.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Auto-setup everything", emoji="⚡", style=discord.ButtonStyle.primary, row=0)
    async def auto(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await self.cog.auto_setup(interaction)

    @discord.ui.button(label="Choose channels manually", emoji="🛠️", row=0)
    async def manual(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(title="🛠️ Choose channels", description="1. Pick what the channel is for.\n2. Pick the channel.", color=utils.BRAND),
            view=ManualView(self.cog, self.owner_id),
        )

    @discord.ui.button(label="Add a Pomodoro room", emoji="🍅", row=1)
    async def pomodoro(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        async def handler(i: discord.Interaction, v: dict) -> None:
            try:
                focus, brk = int(v["focus"]), int(v["break"])
            except ValueError:
                await i.response.send_message("Please enter whole numbers.", ephemeral=True)
                return
            if not 5 <= focus <= 120 or not 1 <= brk <= 60:
                await i.response.send_message("Focus must be 5-120 minutes and the break 1-60.", ephemeral=True)
                return
            guild = i.guild
            if not guild.me.guild_permissions.manage_channels:
                await i.response.send_message("I need the **Manage Channels** permission to create the room.", ephemeral=True)
                return
            category = discord.utils.get(guild.categories, name=CATEGORY_NAME)
            try:
                channel = await guild.create_voice_channel(f"🍅 Pomodoro {focus}-{brk}", category=category, reason="Pomodoro room")
            except discord.HTTPException:
                await i.response.send_message("I couldn't create the channel. Check my permissions.", ephemeral=True)
                return
            await self.cog.bot.get_cog("Pomodoro").add_room(channel, focus, brk)
            await i.response.send_message(f"🍅 Created {channel.mention} - a nonstop {focus}/{brk} cycle.", ephemeral=True)

        await interaction.response.send_modal(QuickModal("New Pomodoro room", [
            ("focus", "Focus minutes (5-120)", {**SHORT, "default": "25", "max_length": 3}),
            ("break", "Break minutes (1-60)", {**SHORT, "default": "5", "max_length": 2}),
        ], handler))

    @discord.ui.button(label="Title roles", emoji="🏅", row=1)
    async def toggle_roles(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = self.cog.bot.db
        now_on = await self.cog._roles_on(interaction.guild_id)
        await db.set_config(interaction.guild_id, "level_roles", "0" if now_on else "1")
        await interaction.response.edit_message(
            embed=await self.cog.status_embed(interaction.guild),
            view=WizardView(self.cog, self.owner_id, not now_on),
        )

    @discord.ui.button(label="Post button panel here", emoji="📌", row=2)
    async def post_panel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        try:
            await interaction.channel.send(embed=hub_embed(), view=HubView())  # type: ignore[union-attr]
        except discord.HTTPException:
            await interaction.response.send_message("I can't post in this channel.", ephemeral=True)
            return
        await interaction.response.send_message("📌 Posted. Pin it so everyone finds it.", ephemeral=True)

    @discord.ui.button(label="Refresh", emoji="🔄", row=2)
    async def refresh(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=await self.cog.status_embed(interaction.guild),
            view=WizardView(self.cog, self.owner_id, await self.cog._roles_on(interaction.guild_id)),
        )


class ManualView(discord.ui.View):
    def __init__(self, cog: Wizard, owner_id: int):
        super().__init__(timeout=300)
        self.cog, self.owner_id = cog, owner_id
        self.kind: str | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id or not interaction.permissions.manage_guild:
            await interaction.response.send_message("Only the admin who opened the wizard can use these.", ephemeral=True)
            return False
        return True

    @discord.ui.select(
        placeholder="1. What is the channel for?", row=0,
        options=[discord.SelectOption(label=label, value=key) for key, label in MANUAL_KINDS],
    )
    async def pick_kind(self, interaction: discord.Interaction, select: discord.ui.Select) -> None:
        self.kind = select.values[0]
        label = dict(MANUAL_KINDS)[self.kind]
        await interaction.response.edit_message(
            embed=discord.Embed(title="🛠️ Choose channels", description=f"Now pick the channel for **{label}** ↓", color=utils.BRAND)
        )

    @discord.ui.select(
        cls=discord.ui.ChannelSelect, channel_types=[discord.ChannelType.text], placeholder="2. Choose the channel", row=1
    )
    async def pick_channel(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect) -> None:
        if self.kind is None:
            await interaction.response.send_message("Pick what the channel is for first (step 1).", ephemeral=True)
            return
        channel = select.values[0]
        db = self.cog.bot.db
        if self.kind == "levelup":
            await db.set_config(interaction.guild_id, "levelup_channel", channel.id)
        else:
            await db.set_setting(interaction.guild_id, dbmod.CHANNEL_KINDS[self.kind], channel.id)
        label = dict(MANUAL_KINDS)[self.kind]
        real = interaction.guild.get_channel(channel.id)
        warning = ""
        if real is not None and not real.permissions_for(interaction.guild.me).send_messages:
            warning = "\n⚠️ I can't send messages there yet - give my role **Send Messages** in that channel."
        await interaction.response.send_message(f"✅ **{label}** → <#{channel.id}>{warning}", ephemeral=True)

    @discord.ui.button(label="Back", emoji="◀️", row=2)
    async def back(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.edit_message(
            embed=await self.cog.status_embed(interaction.guild),
            view=WizardView(self.cog, self.owner_id, await self.cog._roles_on(interaction.guild_id)),
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Wizard(bot))
