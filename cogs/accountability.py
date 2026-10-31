"""Daily goals + check-ins, personal to-do lists, accountability partners."""
from __future__ import annotations

import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import leveling
import utils

log = logging.getLogger(__name__)


async def goal_xp(bot, guild_id: int, user_id: int) -> None:
    """Award goal XP at most once per user per day (so set/done can't be farmed)."""
    if await bot.db.claim(f"goalxp:{guild_id}:{user_id}", utils.today_str()):
        await leveling.grant(bot, guild_id, user_id, leveling.XP_GOAL_DONE, "goal")


class CheckinView(discord.ui.View):
    """Persistent buttons under the evening check-in message."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="I did it!", style=discord.ButtonStyle.success, emoji="✅", custom_id="checkin:done")
    async def done(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        db = interaction.client.db  # type: ignore[attr-defined]
        res = await db.execute(
            "UPDATE goals SET done=1 WHERE guild_id=? AND user_id=? AND day=? AND done=0",
            (interaction.guild_id, interaction.user.id, utils.today_str()),
        )
        if res.rowcount:
            await interaction.response.send_message("🎉 Goal completed! That's how streaks are built.", ephemeral=True)
            await goal_xp(interaction.client, interaction.guild_id, interaction.user.id)
            return
        row = await db.fetchone(
            "SELECT done FROM goals WHERE guild_id=? AND user_id=? AND day=?",
            (interaction.guild_id, interaction.user.id, utils.today_str()),
        )
        if row is None:
            await interaction.response.send_message("You don't have a goal for today. Set one with `/goal set`.", ephemeral=True)
        else:
            await interaction.response.send_message("You've already completed today's goal - well done!", ephemeral=True)

    @discord.ui.button(label="Not yet", style=discord.ButtonStyle.secondary, emoji="⏳", custom_id="checkin:notyet")
    async def not_yet(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_message(
            "No stress. Even 10 focused minutes counts - try `/focus start 10` and finish strong.", ephemeral=True
        )


class Accountability(commands.Cog):
    goal = app_commands.Group(name="goal", description="Your daily goal", guild_only=True)
    todo = app_commands.Group(name="todo", description="Your personal to-do list")
    partner = app_commands.Group(name="partner", description="Find an accountability partner", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self) -> None:
        self.bot.add_view(CheckinView())
        self.checkin_loop.start()

    async def cog_unload(self) -> None:
        self.checkin_loop.cancel()

    # ---- goals -----------------------------------------------------------
    @goal.command(name="set", description="Set your goal for today")
    @app_commands.describe(text="What do you want to get done today?")
    async def goal_set(self, interaction: discord.Interaction, text: app_commands.Range[str, 1, 200]) -> None:
        await self.bot.db.set_goal(interaction.guild_id, interaction.user.id, utils.today_str(), text)
        await interaction.response.send_message(
            f"🎯 Goal locked in: **{text}**\nI'll check in with you this evening. Start a session with `/focus start`."
        )

    @goal.command(name="done", description="Mark today's goal as completed")
    async def goal_done(self, interaction: discord.Interaction) -> None:
        res = await self.bot.db.execute(
            "UPDATE goals SET done=1 WHERE guild_id=? AND user_id=? AND day=? AND done=0",
            (interaction.guild_id, interaction.user.id, utils.today_str()),
        )
        if res.rowcount:
            await interaction.response.send_message(f"✅ {interaction.user.mention} completed today's goal! 🎉")
            await goal_xp(self.bot, interaction.guild_id, interaction.user.id)
        else:
            await interaction.response.send_message(
                "No open goal for today. Set one with `/goal set`.", ephemeral=True
            )

    @goal.command(name="view", description="See today's goal")
    @app_commands.describe(member="Whose goal to view (default: you)")
    async def goal_view(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        member = member or interaction.user  # type: ignore[assignment]
        row = await self.bot.db.fetchone(
            "SELECT text, done FROM goals WHERE guild_id=? AND user_id=? AND day=?",
            (interaction.guild_id, member.id, utils.today_str()),
        )
        if row is None:
            await interaction.response.send_message(f"{member.display_name} hasn't set a goal today.", ephemeral=True)
            return
        status = "✅ done" if row["done"] else "⏳ in progress"
        await interaction.response.send_message(f"**{member.display_name}'s goal** ({status}): {row['text']}")

    # ---- daily check-in --------------------------------------------------
    @tasks.loop(minutes=1)
    async def checkin_loop(self) -> None:
        now = utils.now_local()
        if now.hour != config.CHECKIN_HOUR:
            return
        today = utils.today_str()
        for guild in self.bot.guilds:
            settings = await self.bot.db.get_settings(guild.id)
            channel_id = settings["checkin_channel"]
            if not channel_id:
                continue
            if not await self.bot.db.claim(f"checkin:{guild.id}", today):
                continue
            channel = guild.get_channel(channel_id)
            if channel is None:
                continue
            rows = await self.bot.db.fetchall(
                "SELECT user_id, text FROM goals WHERE guild_id=? AND day=? AND done=0 LIMIT 15",
                (guild.id, today),
            )
            if not rows:
                continue
            lines = [f"<@{r['user_id']}> - {utils.truncate(r['text'], 80)}" for r in rows]
            embed = discord.Embed(
                title="🌙 Evening check-in",
                description="Did you finish your goal today?\n\n" + "\n".join(lines),
                color=utils.PURPLE,
            )
            embed.set_footer(text="Press a button below. No goal yet? Set tomorrow's with /goal set.")
            try:
                await channel.send(embed=embed, view=CheckinView())
            except discord.HTTPException:
                log.warning("Could not post check-in in guild %s", guild.id)

    @checkin_loop.before_loop
    async def _before_checkin(self) -> None:
        await self.bot.wait_until_ready()

    # ---- to-do list ------------------------------------------------------
    @todo.command(name="add", description="Add a task to your to-do list")
    async def todo_add(self, interaction: discord.Interaction, task: app_commands.Range[str, 1, 200]) -> None:
        count = await self.bot.db.scalar("SELECT COUNT(*) FROM todos WHERE user_id=? AND done=0", (interaction.user.id,))
        if (count or 0) >= 50:
            await interaction.response.send_message("You have 50 open tasks - finish a few first! 😅", ephemeral=True)
            return
        await self.bot.db.execute(
            "INSERT INTO todos (user_id, n, text, done, created_at) "
            "SELECT ?, COALESCE(MAX(n), 0) + 1, ?, 0, ? FROM todos WHERE user_id = ?",
            (interaction.user.id, task, utils.now_local().isoformat(timespec="seconds"), interaction.user.id),
        )
        n = await self.bot.db.scalar("SELECT MAX(n) FROM todos WHERE user_id=?", (interaction.user.id,))
        await interaction.response.send_message(f"📝 Added **#{n}**: {task}", ephemeral=True)

    async def _todo_is_public(self, user_id: int) -> bool:
        return await self.bot.db.scalar("SELECT todo_public FROM user_prefs WHERE user_id=?", (user_id,)) == 1

    @todo.command(name="list", description="Show your to-do list, or someone's public list")
    @app_commands.describe(member="Whose list to view (default: you). You can only see lists that are public.")
    async def todo_list(self, interaction: discord.Interaction, member: discord.Member | None = None) -> None:
        target = member or interaction.user
        is_own = target.id == interaction.user.id
        public = await self._todo_is_public(target.id)

        if not is_own and not public:
            await interaction.response.send_message(
                f"🔒 **{target.display_name}**'s to-do list is private.", ephemeral=True
            )
            return

        rows = await self.bot.db.fetchall(
            "SELECT n, text, done FROM todos WHERE user_id=? ORDER BY done, n LIMIT 60", (target.id,)
        )
        if not rows:
            msg = "Your list is empty. Add a task with `/todo add`." if is_own else f"**{target.display_name}**'s list is empty."
            await interaction.response.send_message(msg, ephemeral=True)
            return

        lines = [f"{'✅' if r['done'] else '⬜'} `#{r['n']}` {'~~' + r['text'] + '~~' if r['done'] else r['text']}" for r in rows]
        done_count = sum(1 for r in rows if r["done"])
        title = "📝 Your to-do list" if is_own else f"📝 {target.display_name}'s to-do list"
        embed = discord.Embed(title=title, description=utils.truncate("\n".join(lines), 4000), color=utils.BRAND)
        embed.set_footer(text=f"{'🌐 Public' if public else '🔒 Private'} · {done_count}/{len(rows)} done")
        # A private list is only ever shown to its owner; a public list is posted for everyone.
        await interaction.response.send_message(embed=embed, ephemeral=not public)

    @todo.command(name="visibility", description="Make your to-do list public (everyone can see it) or private")
    @app_commands.describe(mode="Public: anyone can view it with /todo list. Private: only you.")
    @app_commands.choices(
        mode=[
            app_commands.Choice(name="Public - members can view my list", value="public"),
            app_commands.Choice(name="Private - only I can see it", value="private"),
        ]
    )
    async def todo_visibility(self, interaction: discord.Interaction, mode: app_commands.Choice[str]) -> None:
        public = 1 if mode.value == "public" else 0
        await self.bot.db.execute(
            "INSERT INTO user_prefs (user_id, todo_public) VALUES (?, ?) "
            "ON CONFLICT(user_id) DO UPDATE SET todo_public = excluded.todo_public",
            (interaction.user.id, public),
        )
        if public:
            text = (
                "🌐 Your to-do list is now **public**. Any member can see it with `/todo list member:@you`, "
                "and `/todo list` will show it to the whole channel.\nSwitch back anytime with `/todo visibility private`."
            )
        else:
            text = "🔒 Your to-do list is now **private**. Only you can see it."
        await interaction.response.send_message(text, ephemeral=True)

    @todo.command(name="done", description="Mark a task as done")
    @app_commands.describe(number="The task number shown in /todo list")
    async def todo_done(self, interaction: discord.Interaction, number: int) -> None:
        res = await self.bot.db.execute(
            "UPDATE todos SET done=1 WHERE user_id=? AND n=? AND done=0", (interaction.user.id, number)
        )
        msg = f"✅ Task #{number} done. Nice!" if res.rowcount else f"No open task #{number}."
        await interaction.response.send_message(msg, ephemeral=True)

    @todo.command(name="remove", description="Delete a task")
    async def todo_remove(self, interaction: discord.Interaction, number: int) -> None:
        res = await self.bot.db.execute("DELETE FROM todos WHERE user_id=? AND n=?", (interaction.user.id, number))
        msg = f"🗑️ Removed task #{number}." if res.rowcount else f"No task #{number}."
        await interaction.response.send_message(msg, ephemeral=True)

    @todo.command(name="clear_done", description="Delete all completed tasks")
    async def todo_clear(self, interaction: discord.Interaction) -> None:
        res = await self.bot.db.execute("DELETE FROM todos WHERE user_id=? AND done=1", (interaction.user.id,))
        await interaction.response.send_message(f"🧹 Cleared {res.rowcount} completed task(s).", ephemeral=True)

    # ---- accountability partners ----------------------------------------
    @partner.command(name="join", description="Get matched with an accountability partner")
    async def partner_join(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        gid, uid = interaction.guild_id, interaction.user.id
        if await db.scalar("SELECT 1 FROM partners WHERE guild_id=? AND user_id=?", (gid, uid)):
            await interaction.response.send_message("You already have a partner! Check `/partner status`.", ephemeral=True)
            return
        if await db.scalar("SELECT 1 FROM partner_queue WHERE guild_id=? AND user_id=?", (gid, uid)):
            await interaction.response.send_message("You're already in the queue - I'll ping you when someone joins.", ephemeral=True)
            return
        other = await db.scalar(
            "SELECT user_id FROM partner_queue WHERE guild_id=? AND user_id<>? ORDER BY rowid LIMIT 1", (gid, uid)
        )
        if other is None:
            await db.execute("INSERT INTO partner_queue (guild_id, user_id) VALUES (?, ?)", (gid, uid))
            await interaction.response.send_message(
                "🕐 You're in the queue. As soon as another member runs `/partner join`, you'll be matched."
            )
            return
        await db.execute("DELETE FROM partner_queue WHERE guild_id=? AND user_id=?", (gid, other))
        await db.execute("INSERT INTO partners (guild_id, user_id, partner_id) VALUES (?,?,?)", (gid, uid, other))
        await db.execute("INSERT INTO partners (guild_id, user_id, partner_id) VALUES (?,?,?)", (gid, other, uid))
        await interaction.response.send_message(
            f"🤝 <@{uid}> and <@{other}> are now accountability partners!\n"
            "Share your daily goals with each other, start a `/focus` session together, and check in on each other."
        )

    @partner.command(name="leave", description="Leave the queue or end your partnership")
    async def partner_leave(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        gid, uid = interaction.guild_id, interaction.user.id
        await db.execute("DELETE FROM partner_queue WHERE guild_id=? AND user_id=?", (gid, uid))
        partner_id = await db.scalar("SELECT partner_id FROM partners WHERE guild_id=? AND user_id=?", (gid, uid))
        if partner_id is not None:
            await db.execute(
                "DELETE FROM partners WHERE guild_id=? AND user_id IN (?, ?)", (gid, uid, partner_id)
            )
            await interaction.response.send_message(f"Partnership ended. <@{partner_id}> is free to match again.")
        else:
            await interaction.response.send_message("You're out of the queue.", ephemeral=True)

    @partner.command(name="status", description="See how you and your partner are doing today")
    async def partner_status(self, interaction: discord.Interaction) -> None:
        db = self.bot.db
        gid, uid = interaction.guild_id, interaction.user.id
        partner_id = await db.scalar("SELECT partner_id FROM partners WHERE guild_id=? AND user_id=?", (gid, uid))
        if partner_id is None:
            queued = await db.scalar("SELECT 1 FROM partner_queue WHERE guild_id=? AND user_id=?", (gid, uid))
            msg = "You're waiting for a match." if queued else "No partner yet. Run `/partner join`."
            await interaction.response.send_message(msg, ephemeral=True)
            return
        today = utils.today_local()
        lines = []
        for who in (uid, partner_id):
            totals = await db.user_totals(gid, who, today)
            goal = await db.fetchone(
                "SELECT text, done FROM goals WHERE guild_id=? AND user_id=? AND day=?", (gid, who, today.isoformat())
            )
            goal_text = "no goal set" if goal is None else f"{'✅' if goal['done'] else '⏳'} {utils.truncate(goal['text'], 60)}"
            lines.append(f"<@{who}> - {utils.fmt_duration(totals['today'])} today · {goal_text}")
        embed = discord.Embed(title="🤝 Partner check", description="\n".join(lines), color=utils.GREEN)
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Accountability(bot))
