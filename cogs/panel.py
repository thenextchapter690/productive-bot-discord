"""The button-based control panel: every feature reachable by tapping, no commands needed.

`/panel` posts a permanent hub in a channel, `/menu` opens a private copy for anyone, and the
welcome message carries an "Open menu" button. Menus are private (ephemeral) to whoever clicked.

Note: slash commands are reused by calling their `.callback` directly. That skips the checks attached
to them (permissions, cooldowns), so anything restricted re-checks permissions here explicitly.
"""
from __future__ import annotations

import logging
from datetime import timedelta
from urllib.parse import quote_plus

import discord
from discord import app_commands
from discord.ext import commands

import config
import utils
from cogs.environment import AMBIENT
from cogs.flashcards import CardModal
from cogs.dev import RunModal
from cogs.tech import DOCS, RESOURCES, ExplainModal, SnippetModal

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Small UI helpers
# ---------------------------------------------------------------------------
def panel_of(interaction: discord.Interaction) -> "Panel":
    return interaction.client.get_cog("Panel")  # type: ignore[return-value]


def _on_private_menu(interaction: discord.Interaction) -> bool:
    """True when the click came from one of our private (ephemeral) menu messages."""
    msg = interaction.message
    return bool(
        interaction.type == discord.InteractionType.component and msg is not None and msg.flags.ephemeral
    )


async def thinking(interaction: discord.Interaction) -> None:
    """Acknowledge a slow action. Private menus get edited in place; public panels get a private reply."""
    if interaction.response.is_done():
        return
    if _on_private_menu(interaction):
        await interaction.response.defer()
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)


async def present(
    interaction: discord.Interaction,
    *,
    embed: discord.Embed | None = None,
    view: discord.ui.View | None = None,
    file: discord.File | None = None,
) -> None:
    """Show a private screen: edit the current private menu in place, otherwise send a new private message."""
    editing = _on_private_menu(interaction)
    attachments = [file] if file is not None else []
    if editing:
        if interaction.response.is_done():
            await interaction.edit_original_response(embed=embed, view=view, attachments=attachments)
        else:
            await interaction.response.edit_message(embed=embed, view=view, attachments=attachments)
        return
    kwargs: dict = {"ephemeral": True}
    if embed is not None:
        kwargs["embed"] = embed
    if view is not None:
        kwargs["view"] = view
    if file is not None:
        kwargs["file"] = file
    if interaction.response.is_done():
        await interaction.followup.send(**kwargs)
    else:
        await interaction.response.send_message(**kwargs)


async def notify(interaction: discord.Interaction, text: str) -> None:
    """Short private message (never replaces the menu)."""
    if interaction.response.is_done():
        await interaction.followup.send(text, ephemeral=True)
    else:
        await interaction.response.send_message(text, ephemeral=True)


async def report_error(interaction: discord.Interaction, error: Exception) -> None:
    log.exception("Menu action failed", exc_info=error)
    try:
        await notify(interaction, "Something went wrong on my side. Please try again in a moment.")
    except discord.HTTPException:
        pass


class QuickModal(discord.ui.Modal):
    """A small form: fields are (key, label, TextInput options); handler gets {key: stripped value}."""

    def __init__(self, title: str, fields: list[tuple[str, str, dict]], handler):
        super().__init__(title=title[:45])
        self.handler = handler
        self.inputs: dict[str, discord.ui.TextInput] = {}
        for key, label, opts in fields:
            item = discord.ui.TextInput(label=label[:45], **opts)
            self.inputs[key] = item
            self.add_item(item)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.handler(interaction, {k: v.value.strip() for k, v in self.inputs.items()})

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_error(interaction, error)


SHORT = {"style": discord.TextStyle.short}
PARA = {"style": discord.TextStyle.paragraph}


class MenuView(discord.ui.View):
    def __init__(self, panel: "Panel", timeout: float = 600):
        super().__init__(timeout=timeout)
        self.panel = panel

    def add_button(
        self, label: str, callback, *, emoji: str | None = None,
        style: discord.ButtonStyle = discord.ButtonStyle.secondary, row: int | None = None,
    ) -> None:
        button = discord.ui.Button(label=label, emoji=emoji, style=style, row=row)

        async def _callback(interaction: discord.Interaction) -> None:
            await callback(interaction)

        button.callback = _callback
        self.add_item(button)

    def add_back(self, parent=None, row: int | None = None) -> None:
        self.add_button("Back", parent or self.panel.go_home, emoji="◀️", row=row)

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        await report_error(interaction, error)


# ---------------------------------------------------------------------------
# The hub (persistent: survives restarts)
# ---------------------------------------------------------------------------
def hub_embed() -> discord.Embed:
    embed = discord.Embed(
        title="🧠 Productivity Hub",
        description=(
            "Everything this bot can do is **one tap away** - no commands to remember.\n"
            "Press a button below. What opens is private, only you can see it."
        ),
        color=utils.BRAND,
    )
    embed.add_field(
        name="What's inside",
        value=(
            "🎯 **Focus** - start a Pomodoro others can join\n"
            "✅ **Goals & Tasks** - your daily goal and to-do list\n"
            "📊 **My Stats** - your stats card and study heatmap\n"
            "🏆 **Leaderboard** - top studiers and XP\n"
            "🎁 **Daily Bonus** - free XP every day\n"
            "💻 **Tech Tools** - run code, quizzes, docs, news, snippets\n"
            "🌿 **Study Tools** - reminders, polls, sounds, study rooms\n"
            "📚 **Flashcards** - spaced-repetition revision"
        ),
        inline=False,
    )
    embed.set_footer(text="Tip: you can also type / to see every command.")
    return embed


class HubView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Focus", emoji="🎯", style=discord.ButtonStyle.primary, custom_id="hub:focus", row=0)
    async def focus(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).menu_focus(interaction)

    @discord.ui.button(label="Goals & Tasks", emoji="✅", style=discord.ButtonStyle.primary, custom_id="hub:tasks", row=0)
    async def tasks(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).menu_goals(interaction)

    @discord.ui.button(label="My Stats", emoji="📊", style=discord.ButtonStyle.primary, custom_id="hub:stats", row=0)
    async def stats(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).menu_stats(interaction)

    @discord.ui.button(label="Leaderboard", emoji="🏆", style=discord.ButtonStyle.primary, custom_id="hub:board", row=0)
    async def board(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).show_board(interaction, "weekly")

    @discord.ui.button(label="Daily Bonus", emoji="🎁", style=discord.ButtonStyle.success, custom_id="hub:daily", row=0)
    async def daily(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        levels = interaction.client.get_cog("Levels")
        if levels is None:
            await notify(interaction, "The daily bonus isn't available right now.")
            return
        await levels.claim_daily(interaction)

    @discord.ui.button(label="Tech Tools", emoji="💻", style=discord.ButtonStyle.secondary, custom_id="hub:tech", row=1)
    async def tech(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).menu_tech(interaction)

    @discord.ui.button(label="Study Tools", emoji="🌿", style=discord.ButtonStyle.secondary, custom_id="hub:study", row=1)
    async def study(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).menu_study(interaction)

    @discord.ui.button(label="Flashcards", emoji="📚", style=discord.ButtonStyle.secondary, custom_id="hub:cards", row=1)
    async def cards(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).menu_cards(interaction)

    @discord.ui.button(label="Help", emoji="❓", style=discord.ButtonStyle.secondary, custom_id="hub:help", row=1)
    async def help(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await panel_of(interaction).menu_help(interaction)


# ---------------------------------------------------------------------------
class Panel(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(HubView())

    # ---- helpers ---------------------------------------------------------
    def cog(self, name: str):
        return self.bot.get_cog(name)

    def back_view(self, parent=None) -> MenuView:
        view = MenuView(self)
        view.add_back(parent)
        return view

    async def go_home(self, interaction: discord.Interaction) -> None:
        await present(interaction, embed=hub_embed(), view=HubView())

    async def open_main(self, interaction: discord.Interaction) -> None:
        await present(interaction, embed=hub_embed(), view=HubView())

    async def show_text_or_embed(self, interaction: discord.Interaction, result, parent=None) -> None:
        """Show a lookup result (an embed, or an error string) with a Back button."""
        embed = result if isinstance(result, discord.Embed) else discord.Embed(description=str(result), color=utils.ORANGE)
        await present(interaction, embed=embed, view=self.back_view(parent))

    # ---- /menu and /panel --------------------------------------------------
    @app_commands.command(name="menu", description="Open the button menu - no commands to remember")
    @app_commands.guild_only()
    async def menu(self, interaction: discord.Interaction) -> None:
        await self.open_main(interaction)

    @app_commands.command(name="panel", description="Post the permanent button panel in this channel")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    @app_commands.checks.has_permissions(manage_guild=True)
    async def panel(self, interaction: discord.Interaction) -> None:
        try:
            await interaction.channel.send(embed=hub_embed(), view=HubView())  # type: ignore[union-attr]
        except discord.HTTPException:
            await notify(interaction, "I can't post here - I need Send Messages and Embed Links in this channel.")
            return
        await notify(interaction, "✅ Panel posted. Right-click it and **Pin Message** so everyone finds it.")

    # =====================================================================
    # 🎯 Focus
    # =====================================================================
    async def menu_focus(self, interaction: discord.Interaction) -> None:
        focus = self.cog("Focus")
        session = focus.sessions.get(interaction.channel_id or 0) if focus else None
        if session is not None:
            text = (
                f"A **{session.minutes}-minute** session is running here ({len(session.participants)} focusing).\n"
                "Press **Join** on the session message to focus together, or check its status below."
            )
        else:
            text = "No session is running in this channel. Pick a length to start one - others can press **Join** to focus with you."
        embed = discord.Embed(title="🎯 Focus", description=text, color=utils.GREEN)
        embed.set_footer(text="Time and XP are saved automatically.")
        view = MenuView(self)
        view.add_button("25 min", lambda i: self.start_focus(i, 25, 5), emoji="🍅", style=discord.ButtonStyle.success, row=0)
        view.add_button("50 min", lambda i: self.start_focus(i, 50, 10), emoji="🔥", style=discord.ButtonStyle.success, row=0)
        view.add_button("90 min", lambda i: self.start_focus(i, 90, 15), emoji="🧠", style=discord.ButtonStyle.success, row=0)
        view.add_button("Custom", self.focus_custom, emoji="⚙️", row=0)
        view.add_button("Session status", self.focus_status, emoji="👀", row=1)
        view.add_button("Stop session", self.focus_stop, emoji="🛑", style=discord.ButtonStyle.danger, row=1)
        view.add_back(row=2)
        await present(interaction, embed=embed, view=view)

    async def start_focus(self, interaction: discord.Interaction, minutes: int, break_minutes: int) -> None:
        focus = self.cog("Focus")
        if focus is None:
            await notify(interaction, "Focus sessions aren't available right now.")
            return
        await focus.begin(interaction, minutes, break_minutes)

    async def focus_custom(self, interaction: discord.Interaction) -> None:
        async def handler(i: discord.Interaction, v: dict) -> None:
            try:
                minutes, brk = int(v["minutes"]), int(v["break"])
            except ValueError:
                await notify(i, "Please enter whole numbers, like 40 and 8.")
                return
            if not 1 <= minutes <= 180 or not 0 <= brk <= 60:
                await notify(i, "Focus must be 1-180 minutes and the break 0-60 minutes.")
                return
            await self.start_focus(i, minutes, brk)

        await interaction.response.send_modal(QuickModal("Custom focus session", [
            ("minutes", "Focus minutes (1-180)", {**SHORT, "default": "25", "max_length": 3}),
            ("break", "Break minutes (0-60)", {**SHORT, "default": "5", "max_length": 2}),
        ], handler))

    async def focus_status(self, interaction: discord.Interaction) -> None:
        focus = self.cog("Focus")
        session = focus.sessions.get(interaction.channel_id or 0) if focus else None
        if session is None:
            await notify(interaction, "No session is running in this channel.")
            return
        await interaction.response.send_message(embed=session.embed(), ephemeral=True)

    async def focus_stop(self, interaction: discord.Interaction) -> None:
        focus = self.cog("Focus")
        if focus is None:
            await notify(interaction, "Focus sessions aren't available right now.")
            return
        await focus.focus_stop.callback(focus, interaction)

    # =====================================================================
    # ✅ Goals & tasks
    # =====================================================================
    async def menu_goals(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        goal = await db.fetchone(
            "SELECT text, done FROM goals WHERE guild_id=? AND user_id=? AND day=?",
            (interaction.guild_id, interaction.user.id, utils.today_str()),
        )
        open_tasks = await db.scalar("SELECT COUNT(*) FROM todos WHERE user_id=? AND done=0", (interaction.user.id,))
        if goal is None:
            goal_line = "No goal set for today yet."
        else:
            goal_line = f"{'✅' if goal['done'] else '⏳'} {utils.truncate(goal['text'], 120)}"
        embed = discord.Embed(
            title="✅ Goals & Tasks",
            description=f"**Today's goal:** {goal_line}\n**Open tasks:** {open_tasks or 0}",
            color=utils.BRAND,
        )
        view = MenuView(self)
        view.add_button("Set today's goal", self.goal_set, emoji="🎯", style=discord.ButtonStyle.primary, row=0)
        view.add_button("Mark goal done", self.goal_done, emoji="✅", style=discord.ButtonStyle.success, row=0)
        view.add_button("Add task", self.task_add, emoji="➕", row=1)
        view.add_button("My tasks", self.show_tasks, emoji="📝", row=1)
        view.add_back(row=2)
        await present(interaction, embed=embed, view=view)

    async def goal_set(self, interaction: discord.Interaction) -> None:
        acc = self.cog("Accountability")

        async def handler(i: discord.Interaction, v: dict) -> None:
            if not v["goal"]:
                await notify(i, "Write your goal first.")
                return
            await acc.goal_set.callback(acc, i, v["goal"])

        await interaction.response.send_modal(QuickModal("Today's goal", [
            ("goal", "What do you want to get done today?", {**PARA, "max_length": 200, "placeholder": "Finish chapter 3 notes"}),
        ], handler))

    async def goal_done(self, interaction: discord.Interaction) -> None:
        acc = self.cog("Accountability")
        await acc.goal_done.callback(acc, interaction)

    async def task_add(self, interaction: discord.Interaction) -> None:
        acc = self.cog("Accountability")

        async def handler(i: discord.Interaction, v: dict) -> None:
            if not v["task"]:
                await notify(i, "Write the task first.")
                return
            await acc.todo_add.callback(acc, i, v["task"])

        await interaction.response.send_modal(QuickModal("New task", [
            ("task", "Task", {**SHORT, "max_length": 200, "placeholder": "Solve 3 DSA problems"}),
        ], handler))

    async def show_tasks(self, interaction: discord.Interaction) -> None:
        acc = self.cog("Accountability")
        uid = interaction.user.id
        rows = await self.bot.db.fetchall(
            "SELECT n, text, done FROM todos WHERE user_id=? ORDER BY done, n LIMIT 60", (uid,)
        )
        public = await acc._todo_is_public(uid)
        if rows:
            lines = [f"{'✅' if r['done'] else '⬜'} `#{r['n']}` {'~~' + r['text'] + '~~' if r['done'] else r['text']}" for r in rows]
            body = utils.truncate("\n".join(lines), 3800)
        else:
            body = "No tasks yet. Press **Add task** to start your list."
        embed = discord.Embed(title="📝 Your tasks", description=body, color=utils.BRAND)
        embed.set_footer(text=f"{'🌐 Public - members can view it' if public else '🔒 Private - only you'}")

        view = MenuView(self)
        open_rows = [r for r in rows if not r["done"]][:25]
        if open_rows:
            select = discord.ui.Select(
                placeholder="Tick off a task…",
                options=[discord.SelectOption(label=utils.truncate(f"#{r['n']}  {r['text']}", 100), value=str(r["n"])) for r in open_rows],
                row=0,
            )

            async def on_pick(i: discord.Interaction) -> None:
                await self.bot.db.execute("UPDATE todos SET done=1 WHERE user_id=? AND n=?", (i.user.id, int(select.values[0])))
                await self.show_tasks(i)

            select.callback = on_pick
            view.add_item(select)

        async def toggle(i: discord.Interaction) -> None:
            await self.bot.db.execute(
                "INSERT INTO user_prefs (user_id, todo_public) VALUES (?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET todo_public = excluded.todo_public",
                (i.user.id, 0 if public else 1),
            )
            await self.show_tasks(i)

        async def clear_done(i: discord.Interaction) -> None:
            await self.bot.db.execute("DELETE FROM todos WHERE user_id=? AND done=1", (i.user.id,))
            await self.show_tasks(i)

        view.add_button("Add task", self.task_add, emoji="➕", row=1)
        view.add_button("Make private" if public else "Make public", toggle, emoji="🔒" if public else "🌐", row=1)
        view.add_button("Clear done", clear_done, emoji="🧹", row=1)
        view.add_back(self.menu_goals, row=2)
        await present(interaction, embed=embed, view=view)

    # =====================================================================
    # 📊 Stats and 🏆 leaderboard
    # =====================================================================
    async def menu_stats(self, interaction: discord.Interaction) -> None:
        tracking = self.cog("Tracking")
        if tracking is None:
            await notify(interaction, "Stats aren't available right now.")
            return
        await thinking(interaction)
        member = interaction.user
        data = await tracking.collect_stats(interaction.guild_id, member)
        file = await tracking.stats_card_file(member, data)
        view = MenuView(self)
        view.add_button("Share in channel", self.share_stats, emoji="📣", style=discord.ButtonStyle.primary)
        view.add_button("Leaderboard", lambda i: self.show_board(i, "weekly"), emoji="🏆")
        view.add_back()
        if file is not None:
            await present(interaction, view=view, file=file)
        else:
            await present(interaction, embed=tracking.stats_embed(member, data), view=view)

    async def share_stats(self, interaction: discord.Interaction) -> None:
        tracking = self.cog("Tracking")
        await interaction.response.defer()
        member = interaction.user
        data = await tracking.collect_stats(interaction.guild_id, member)
        file = await tracking.stats_card_file(member, data)
        try:
            if file is not None:
                await interaction.channel.send(f"📊 **{member.display_name}** shared their stats:", file=file)  # type: ignore[union-attr]
            else:
                await interaction.channel.send(embed=tracking.stats_embed(member, data))  # type: ignore[union-attr]
        except discord.HTTPException:
            await interaction.followup.send("I can't post in this channel.", ephemeral=True)
            return
        await interaction.followup.send("📣 Shared in the channel!", ephemeral=True)

    BOARD_KINDS = [("daily", "Today"), ("weekly", "7 days"), ("monthly", "Month"), ("all", "All time"), ("xp", "XP")]

    async def board_embed(self, guild: discord.Guild, kind: str) -> discord.Embed:
        if kind == "xp":
            return await self.cog("Levels").levels_embed(guild)
        today = utils.today_local()
        since = {
            "daily": today.isoformat(),
            "weekly": (today - timedelta(days=6)).isoformat(),
            "monthly": today.replace(day=1).isoformat(),
            "all": "0000-00-00",
        }[kind]
        label = dict(self.BOARD_KINDS)[kind]
        rows = await self.bot.db.leaderboard(guild.id, since, 10)
        if not rows:
            return discord.Embed(
                title=f"🏆 Leaderboard · {label}", description="Nobody has logged time for this period yet. Be the first! 🏁",
                color=utils.ORANGE,
            )
        medals = ["🥇", "🥈", "🥉"]
        lines = []
        for i, row in enumerate(rows):
            member = guild.get_member(row["user_id"])
            name = member.display_name if member else f"User {row['user_id']}"
            lines.append(f"{medals[i] if i < 3 else f'`{i + 1}.`'} **{name}** - {utils.fmt_duration(row['total'])}")
        return discord.Embed(title=f"🏆 Leaderboard · {label}", description="\n".join(lines), color=utils.ORANGE)

    async def show_board(self, interaction: discord.Interaction, kind: str) -> None:
        embed = await self.board_embed(interaction.guild, kind)
        view = MenuView(self)
        for key, label in self.BOARD_KINDS:
            view.add_button(
                label, (lambda k: (lambda i: self.show_board(i, k)))(key),
                style=discord.ButtonStyle.primary if key == kind else discord.ButtonStyle.secondary, row=0,
            )

        async def share(i: discord.Interaction) -> None:
            try:
                await i.channel.send(embed=embed)  # type: ignore[union-attr]
            except discord.HTTPException:
                await notify(i, "I can't post in this channel.")
                return
            await notify(i, "📣 Shared in the channel!")

        view.add_button("Share in channel", share, emoji="📣", row=1)
        view.add_back(row=1)
        await present(interaction, embed=embed, view=view)

    # =====================================================================
    # 💻 Tech tools
    # =====================================================================
    async def menu_tech(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="💻 Tech Tools",
            description="Pick a tool. Results are private unless it says it will post in the channel.",
            color=utils.PURPLE,
        )
        view = MenuView(self)
        view.add_button("Daily challenge", self.tech_challenge, emoji="🧩", row=0)
        view.add_button("Tech news", self.tech_news, emoji="📰", row=0)
        view.add_button("Trending repos", self.tech_trending, emoji="🔭", row=0)
        view.add_button("Learning paths", self.tech_resources, emoji="🗺️", row=0)
        view.add_button("Docs", self.tech_docs, emoji="📚", row=0)
        view.add_button("Run code", self.tech_run, emoji="▶️", style=discord.ButtonStyle.success, row=1)
        view.add_button("Explain error", self.tech_explain, emoji="🤖", row=1)
        view.add_button("Quiz me", self.tech_quiz, emoji="🧠", row=1)
        view.add_button("Save snippet", self.tech_snippet_save, emoji="💾", row=1)
        view.add_button("Browse snippets", self.tech_snippet_browse, emoji="🗂️", row=1)
        view.add_button("GitHub lookup", self.tech_github, emoji="🐙", row=2)
        view.add_button("PyPI lookup", self.tech_pypi, emoji="📦", row=2)
        view.add_button("Ask for code review", self.tech_review, emoji="🔍", row=2)
        view.add_back(row=2)
        await present(interaction, embed=embed, view=view)

    async def tech_challenge(self, interaction: discord.Interaction) -> None:
        await self.show_text_or_embed(interaction, self.cog("Tech")._challenge_embed(), self.menu_tech)

    async def tech_news(self, interaction: discord.Interaction) -> None:
        await thinking(interaction)
        embed = await self.cog("Tech")._hn_embed()
        await self.show_text_or_embed(interaction, embed or "Couldn't reach Hacker News right now. Try again in a minute.", self.menu_tech)

    async def tech_trending(self, interaction: discord.Interaction) -> None:
        await thinking(interaction)
        await self.show_text_or_embed(interaction, await self.cog("Tech").trending_result(None), self.menu_tech)

    async def tech_resources(self, interaction: discord.Interaction) -> None:
        select = discord.ui.Select(
            placeholder="What do you want to learn?",
            options=[discord.SelectOption(label=label, value=key) for key, (label, _) in RESOURCES.items()],
        )

        async def on_pick(i: discord.Interaction) -> None:
            label, links = RESOURCES[select.values[0]]
            embed = discord.Embed(
                title=f"🗺️ Learn: {label}", description="\n".join(f"• [{t}]({u})" for t, u in links), color=utils.GREEN
            )
            await self.show_text_or_embed(i, embed, self.menu_tech)

        select.callback = on_pick
        view = self.back_view(self.menu_tech)
        view.add_item(select)
        await present(interaction, embed=discord.Embed(title="🗺️ Learning paths", description="Choose a topic.", color=utils.GREEN), view=view)

    async def tech_docs(self, interaction: discord.Interaction) -> None:
        async def handler(i: discord.Interaction, v: dict) -> None:
            wanted = v["language"].lower().replace(" ", "")
            key = wanted if wanted in DOCS else next(
                (k for k, (label, _) in DOCS.items() if wanted and (wanted in k or wanted in label.lower().replace(" ", ""))), None
            )
            if key is None or not v["query"]:
                await notify(i, "I know docs for: " + ", ".join(f"`{k}`" for k in DOCS) + ". Fill in both boxes.")
                return
            label, template = DOCS[key]
            embed = discord.Embed(
                title=f"📚 {label}: {v['query']}",
                description=(
                    f"[Search the {label} docs]({template.format(q=quote_plus(v['query']))})\n"
                    f"[Search Stack Overflow](https://stackoverflow.com/search?q={quote_plus(key + ' ' + v['query'])})"
                ),
                color=utils.BRAND,
            )
            await present(i, embed=embed, view=self.back_view(self.menu_tech))

        await interaction.response.send_modal(QuickModal("Look up docs", [
            ("language", "Language or tool", {**SHORT, "max_length": 20, "placeholder": "python, javascript, rust, docker ..."}),
            ("query", "What to look up", {**SHORT, "max_length": 100, "placeholder": "list.sort"}),
        ], handler))

    async def tech_run(self, interaction: discord.Interaction) -> None:
        dev = self.cog("Dev")
        if not config.PISTON_URL or dev is None:
            await notify(interaction, "The code runner is turned off on this bot.")
            return
        await interaction.response.send_modal(RunModal(dev))

    async def tech_explain(self, interaction: discord.Interaction) -> None:
        tech = self.cog("Tech")
        if not config.AI_ENABLED or tech is None:
            await notify(interaction, "The AI helper isn't set up on this bot. The owner can enable it with `AI_API_KEY`, `AI_BASE_URL` and `AI_MODEL`.")
            return
        await interaction.response.send_modal(ExplainModal(tech))

    async def tech_quiz(self, interaction: discord.Interaction) -> None:
        cards = self.cog("Flashcards")

        async def handler(i: discord.Interaction, v: dict) -> None:
            if len(v["topic"]) < 2:
                await notify(i, "Tell me what to quiz you on.")
                return
            try:
                count = max(3, min(8, int(v["count"] or 5)))
            except ValueError:
                count = 5
            await cards.start_quiz(i, v["topic"], count)

        await interaction.response.send_modal(QuickModal("AI quiz", [
            ("topic", "Topic", {**SHORT, "max_length": 100, "placeholder": "Python decorators, TCP, cell biology ..."}),
            ("count", "Number of questions (3-8)", {**SHORT, "default": "5", "max_length": 1, "required": False}),
        ], handler))

    async def tech_snippet_save(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SnippetModal(self.bot))

    async def tech_snippet_browse(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(
            "SELECT name, language FROM snippets WHERE guild_id=? ORDER BY name LIMIT 25", (interaction.guild_id,)
        )
        if not rows:
            await self.show_text_or_embed(interaction, "No snippets yet. Press **Save snippet** to add the first one.", self.menu_tech)
            return
        select = discord.ui.Select(
            placeholder="Choose a snippet",
            options=[discord.SelectOption(label=r["name"], description=r["language"] or None, value=r["name"]) for r in rows],
        )

        async def on_pick(i: discord.Interaction) -> None:
            row = await self.bot.db.fetchone(
                "SELECT * FROM snippets WHERE guild_id=? AND name=?", (i.guild_id, select.values[0])
            )
            if row is None:
                await notify(i, "That snippet no longer exists.")
                return
            code = row["code"].replace("```", "``​`")
            embed = discord.Embed(
                title=f"💾 {row['name']}", description=f"```{row['language'] or ''}\n{code}\n```", color=utils.BRAND
            )
            embed.set_footer(text="Use /snippet get to post it in the channel.")
            await present(i, embed=embed, view=self.back_view(self.tech_snippet_browse))

        select.callback = on_pick
        view = self.back_view(self.menu_tech)
        view.add_item(select)
        await present(interaction, embed=discord.Embed(title="🗂️ Snippets", description="Pick one to view it.", color=utils.BRAND), view=view)

    async def tech_github(self, interaction: discord.Interaction) -> None:
        dev = self.cog("Dev")

        async def handler(i: discord.Interaction, v: dict) -> None:
            await thinking(i)
            await self.show_text_or_embed(i, await dev.github_embed(v["query"]), self.menu_tech)

        await interaction.response.send_modal(QuickModal("GitHub lookup", [
            ("query", "Username or owner/repo", {**SHORT, "max_length": 120, "placeholder": "torvalds   or   python/cpython"}),
        ], handler))

    async def tech_pypi(self, interaction: discord.Interaction) -> None:
        dev = self.cog("Dev")

        async def handler(i: discord.Interaction, v: dict) -> None:
            await thinking(i)
            await self.show_text_or_embed(i, await dev.pypi_embed(v["name"]), self.menu_tech)

        await interaction.response.send_modal(QuickModal("PyPI lookup", [
            ("name", "Package name", {**SHORT, "max_length": 100, "placeholder": "requests"}),
        ], handler))

    async def tech_review(self, interaction: discord.Interaction) -> None:
        tech = self.cog("Tech")

        async def handler(i: discord.Interaction, v: dict) -> None:
            if len(v["title"]) < 3:
                await notify(i, "Give the review request a title (at least 3 characters).")
                return
            await tech.review.callback(tech, i, v["title"], v["link"] or None, v["details"] or None)

        await interaction.response.send_modal(QuickModal("Ask for a code review", [
            ("title", "What should people review?", {**SHORT, "max_length": 80}),
            ("link", "Link (GitHub, gist, playground)", {**SHORT, "max_length": 300, "required": False}),
            ("details", "What feedback do you want?", {**PARA, "max_length": 500, "required": False}),
        ], handler))

    # =====================================================================
    # 🌿 Study tools
    # =====================================================================
    async def menu_study(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="🌿 Study Tools",
            description="Reminders, polls, focus sounds, accountability partners and your private study room.",
            color=utils.GREEN,
        )
        view = MenuView(self)
        view.add_button("Remind me", self.study_remind, emoji="⏰", row=0)
        view.add_button("My reminders", self.study_reminders, emoji="📋", row=0)
        view.add_button("New poll", self.study_poll, emoji="📊", row=0)
        view.add_button("Focus sounds", self.study_sounds, emoji="🎧", row=0)
        view.add_button("Find a partner", self.study_partner, emoji="🤝", row=1)
        view.add_button("Partner status", self.study_partner_status, emoji="👥", row=1)
        view.add_button("Study rooms", self.study_rooms_info, emoji="📚", row=1)
        view.add_button("My room controls", self.menu_room, emoji="🎛️", row=1)
        view.add_button("Quiet hours (mods)", self.study_quiet, emoji="🤫", row=2)
        view.add_back(row=2)
        await present(interaction, embed=embed, view=view)

    async def study_remind(self, interaction: discord.Interaction) -> None:
        env = self.cog("Environment")

        async def handler(i: discord.Interaction, v: dict) -> None:
            if not v["message"]:
                await notify(i, "What should I remind you about?")
                return
            await env.remind_add.callback(env, i, v["when"], v["message"], False)

        await interaction.response.send_modal(QuickModal("Set a reminder", [
            ("when", "In how long?", {**SHORT, "max_length": 12, "placeholder": "30m, 2h, 1d, 1h30m"}),
            ("message", "Remind me to…", {**SHORT, "max_length": 300}),
        ], handler))

    async def study_reminders(self, interaction: discord.Interaction) -> None:
        env = self.cog("Environment")
        await env.remind_list.callback(env, interaction)

    async def study_poll(self, interaction: discord.Interaction) -> None:
        env = self.cog("Environment")

        async def handler(i: discord.Interaction, v: dict) -> None:
            await env.poll.callback(env, i, v["question"], v["options"])

        await interaction.response.send_modal(QuickModal("Create a poll", [
            ("question", "Question", {**SHORT, "max_length": 200}),
            ("options", "Options separated by |", {**SHORT, "max_length": 600, "placeholder": "Pizza | Pasta | Salad"}),
        ], handler))

    async def study_sounds(self, interaction: discord.Interaction) -> None:
        select = discord.ui.Select(
            placeholder="Pick a vibe",
            options=[discord.SelectOption(label=label, value=key) for key, (label, _) in AMBIENT.items()],
        )

        async def on_pick(i: discord.Interaction) -> None:
            label, links = AMBIENT[select.values[0]]
            embed = discord.Embed(title=f"🎧 {label}", description="\n".join(f"• [{t}]({u})" for t, u in links), color=utils.PURPLE)
            await self.show_text_or_embed(i, embed, self.menu_study)

        select.callback = on_pick
        view = self.back_view(self.menu_study)
        view.add_item(select)
        await present(interaction, embed=discord.Embed(title="🎧 Focus sounds", description="Choose a vibe.", color=utils.PURPLE), view=view)

    async def study_partner(self, interaction: discord.Interaction) -> None:
        acc = self.cog("Accountability")
        await acc.partner_join.callback(acc, interaction)

    async def study_partner_status(self, interaction: discord.Interaction) -> None:
        acc = self.cog("Accountability")
        await acc.partner_status.callback(acc, interaction)

    async def study_rooms_info(self, interaction: discord.Interaction) -> None:
        settings = await self.bot.db.get_settings(interaction.guild_id)
        rooms = await self.bot.db.fetchall("SELECT * FROM pomodoro_rooms WHERE guild_id=?", (interaction.guild_id,))
        lines = []
        if settings["lobby_channel"]:
            lines.append(f"➕ **Private study room:** join <#{settings['lobby_channel']}> and I'll create a room just for you. "
                         "Lock it, invite friends and rename it from **My room controls**.")
        else:
            lines.append("➕ **Private study rooms** aren't set up yet - ask an admin to run `/wizard`.")
        if rooms:
            lines.append("🍅 **Nonstop Pomodoro rooms:** " + ", ".join(f"<#{r['channel_id']}> ({r['focus_min']}/{r['break_min']})" for r in rooms)
                         + "\nJump in any time - the timer never stops and I post each phase in the room's chat.")
        lines.append("⏱️ All time in voice counts toward your stats and XP automatically.")
        await self.show_text_or_embed(interaction, discord.Embed(title="📚 Study rooms", description="\n\n".join(lines), color=utils.GREEN), self.menu_study)

    async def study_quiet(self, interaction: discord.Interaction) -> None:
        if not interaction.permissions.manage_channels:
            await notify(interaction, "🔒 Only moderators with **Manage Channels** can start quiet hours.")
            return
        env = self.cog("Environment")

        async def handler(i: discord.Interaction, v: dict) -> None:
            if not i.permissions.manage_channels:
                await notify(i, "🔒 Only moderators with **Manage Channels** can start quiet hours.")
                return
            await env.quiet_on.callback(env, i, v["duration"] or "1h", None)

        await interaction.response.send_modal(QuickModal("Quiet hours for this channel", [
            ("duration", "How long? (up to 24h)", {**SHORT, "default": "1h", "max_length": 10}),
        ], handler))

    async def menu_room(self, interaction: discord.Interaction) -> None:
        env = self.cog("Environment")
        embed = discord.Embed(
            title="🎛️ My room controls",
            description="Join your private study room first, then use these.\n(Make one by joining the **➕ Create a study room** voice channel.)",
            color=utils.GREEN,
        )
        view = MenuView(self)
        view.add_button("Lock", lambda i: env.room_lock.callback(env, i), emoji="🔒", row=0)
        view.add_button("Unlock", lambda i: env.room_unlock.callback(env, i), emoji="🔓", row=0)
        view.add_button("Rename", self.room_rename, emoji="✏️", row=0)
        view.add_button("Set limit", self.room_limit, emoji="👥", row=0)

        picker = discord.ui.UserSelect(placeholder="Invite someone to your locked room…", row=1)

        async def on_invite(i: discord.Interaction) -> None:
            await env.room_invite.callback(env, i, picker.values[0])

        picker.callback = on_invite
        view.add_item(picker)
        view.add_back(self.menu_study, row=2)
        await present(interaction, embed=embed, view=view)

    async def room_rename(self, interaction: discord.Interaction) -> None:
        env = self.cog("Environment")

        async def handler(i: discord.Interaction, v: dict) -> None:
            if not v["name"]:
                await notify(i, "Enter a name.")
                return
            await env.room_rename.callback(env, i, v["name"][:60])

        await interaction.response.send_modal(QuickModal("Rename my room", [
            ("name", "New name", {**SHORT, "max_length": 60}),
        ], handler))

    async def room_limit(self, interaction: discord.Interaction) -> None:
        env = self.cog("Environment")

        async def handler(i: discord.Interaction, v: dict) -> None:
            try:
                people = int(v["people"])
            except ValueError:
                await notify(i, "Enter a number from 0 to 99 (0 = unlimited).")
                return
            if not 0 <= people <= 99:
                await notify(i, "Enter a number from 0 to 99 (0 = unlimited).")
                return
            await env.room_limit.callback(env, i, people)

        await interaction.response.send_modal(QuickModal("Room size limit", [
            ("people", "Max people (0 = unlimited)", {**SHORT, "max_length": 2}),
        ], handler))

    # =====================================================================
    # 📚 Flashcards
    # =====================================================================
    async def menu_cards(self, interaction: discord.Interaction) -> None:
        cards = self.cog("Flashcards")
        total, due = await cards.counts(interaction.user.id)
        embed = discord.Embed(
            title="📚 Flashcards",
            description=(
                f"You have **{total}** card(s), **{due}** due now.\n\n"
                "Add what you want to remember, then review a little every day. "
                "Cards you know come back later; cards you miss come back sooner."
            ),
            color=utils.BRAND,
        )
        view = MenuView(self)
        view.add_button("Add card", self.cards_add, emoji="➕", style=discord.ButtonStyle.primary, row=0)
        view.add_button(f"Review now ({due})", self.cards_review, emoji="🧠", style=discord.ButtonStyle.success, row=0)
        view.add_button("My deck", self.cards_deck, emoji="🗂️", row=1)
        view.add_button("AI quiz", self.tech_quiz, emoji="🤖", row=1)
        view.add_back(row=2)
        await present(interaction, embed=embed, view=view)

    async def cards_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CardModal(self.cog("Flashcards")))

    async def cards_review(self, interaction: discord.Interaction) -> None:
        embed, view = await self.cog("Flashcards").review_start(interaction.user.id)
        if view is None:
            await present(interaction, embed=embed, view=self.back_view(self.menu_cards))
        else:
            await present(interaction, embed=embed, view=view)

    async def cards_deck(self, interaction: discord.Interaction) -> None:
        cards = self.cog("Flashcards")
        await cards.cards_list.callback(cards, interaction)

    # =====================================================================
    # ❓ Help
    # =====================================================================
    async def menu_help(self, interaction: discord.Interaction) -> None:
        embed = discord.Embed(
            title="❓ How this works",
            description=(
                "**Never typed a bot command? You don't have to.**\n"
                "Press **Back** to return to the main menu, and tap the buttons - forms pop up when I need details.\n\n"
                "• **Focus** - start a timed session; friends can join\n"
                "• **Stats & Leaderboard** - time in voice and focus sessions is tracked for you\n"
                "• **XP & levels** - earn XP for studying, finishing goals and quizzes; claim the **Daily Bonus**\n"
                "• Everything private to you shows only on your screen\n\n"
                "Power users can type `/` to see every command, or `/menu` to reopen this."
            ),
            color=utils.BRAND,
        )
        await present(interaction, embed=embed, view=self.back_view())


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Panel(bot))
