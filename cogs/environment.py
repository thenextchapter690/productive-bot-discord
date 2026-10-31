"""Study environment: reminders, temporary study rooms, quiet hours, ambient sounds,
daily 'what are you working on' thread, polls."""
from __future__ import annotations

import logging
import time

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
import utils

log = logging.getLogger(__name__)

NUMBER_EMOJI = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣"]

AMBIENT = {
    "lofi": ("Lo-fi beats", [
        ("YouTube: lofi study music", "https://www.youtube.com/results?search_query=lofi+study+music"),
    ]),
    "rain": ("Rain & storms", [
        ("Rainy Mood", "https://www.rainymood.com/"),
        ("YouTube: rain sounds for studying", "https://www.youtube.com/results?search_query=rain+sounds+for+studying"),
    ]),
    "noise": ("White / brown noise", [
        ("Noisli", "https://www.noisli.com/"),
        ("myNoise", "https://mynoise.net/"),
    ]),
    "classical": ("Classical & instrumental", [
        ("YouTube: classical music for focus", "https://www.youtube.com/results?search_query=classical+music+for+focus"),
    ]),
    "synthwave": ("Synthwave / coding", [
        ("YouTube: synthwave coding music", "https://www.youtube.com/results?search_query=synthwave+coding+music"),
    ]),
}

ROOM_COOLDOWN = 8.0  # seconds between room creations per user


class Environment(commands.Cog):
    remind = app_commands.Group(name="remind", description="Reminders")
    room = app_commands.Group(name="room", description="Manage your temporary study room", guild_only=True)
    quiet = app_commands.Group(
        name="quiet", description="Quiet hours for focus", guild_only=True,
        default_permissions=discord.Permissions(manage_channels=True),
    )

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_create: dict[int, float] = {}

    async def cog_load(self) -> None:
        self.reminder_loop.start()
        self.quiet_loop.start()
        self.daily_loop.start()

    async def cog_unload(self) -> None:
        self.reminder_loop.cancel()
        self.quiet_loop.cancel()
        self.daily_loop.cancel()

    # ======================================================================
    # Reminders
    # ======================================================================
    @remind.command(name="add", description="Remind me about something later")
    @app_commands.describe(
        when="How long from now: 30m, 2h, 1d, 1h30m", message="What should I remind you about?",
        private="Send the reminder to your DMs instead of this channel",
    )
    async def remind_add(
        self, interaction: discord.Interaction, when: str, message: app_commands.Range[str, 1, 300], private: bool = False
    ) -> None:
        seconds = utils.parse_duration(when)
        if seconds is None or seconds < 10:
            await interaction.response.send_message("I couldn't read that time. Try `30m`, `2h` or `1d`.", ephemeral=True)
            return
        if seconds > 30 * 86400:
            await interaction.response.send_message("Reminders can be at most 30 days away.", ephemeral=True)
            return
        count = await self.bot.db.scalar("SELECT COUNT(*) FROM reminders WHERE user_id=?", (interaction.user.id,))
        if (count or 0) >= 25:
            await interaction.response.send_message("You have 25 pending reminders - cancel some first.", ephemeral=True)
            return
        due = int(time.time() + seconds)
        channel_id = None if private else interaction.channel_id
        res = await self.bot.db.execute(
            "INSERT INTO reminders (user_id, guild_id, channel_id, message, due_ts) VALUES (?,?,?,?,?)",
            (interaction.user.id, interaction.guild_id, channel_id, message, due),
        )
        await interaction.response.send_message(f"⏰ Got it! I'll remind you <t:{due}:R> (id `{res.lastrowid}`).", ephemeral=private)

    @remind.command(name="list", description="Show your pending reminders")
    async def remind_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(
            "SELECT id, message, due_ts FROM reminders WHERE user_id=? ORDER BY due_ts LIMIT 25", (interaction.user.id,)
        )
        if not rows:
            await interaction.response.send_message("No pending reminders.", ephemeral=True)
            return
        lines = [f"`{r['id']}` <t:{r['due_ts']}:R> - {utils.truncate(r['message'], 80)}" for r in rows]
        await interaction.response.send_message(embed=discord.Embed(
            title="⏰ Your reminders", description="\n".join(lines), color=utils.BRAND), ephemeral=True)

    @remind.command(name="cancel", description="Cancel a reminder")
    @app_commands.describe(reminder_id="The id shown in /remind list")
    async def remind_cancel(self, interaction: discord.Interaction, reminder_id: int) -> None:
        res = await self.bot.db.execute("DELETE FROM reminders WHERE id=? AND user_id=?", (reminder_id, interaction.user.id))
        await interaction.response.send_message("🗑️ Cancelled." if res.rowcount else "No such reminder.", ephemeral=True)

    @tasks.loop(seconds=15)
    async def reminder_loop(self) -> None:
        rows = await self.bot.db.fetchall(
            "SELECT * FROM reminders WHERE due_ts <= ? ORDER BY due_ts LIMIT 25", (int(time.time()),)
        )
        for row in rows:
            await self.bot.db.execute("DELETE FROM reminders WHERE id=?", (row["id"],))
            await self._deliver(row)

    @reminder_loop.before_loop
    async def _before_reminders(self) -> None:
        await self.bot.wait_until_ready()

    async def _deliver(self, row) -> None:
        user_id = row["user_id"]
        text = f"⏰ <@{user_id}> reminder: {row['message']}"
        if row["channel_id"]:
            channel = self.bot.get_channel(row["channel_id"])
            if channel is not None:
                try:
                    await channel.send(text, allowed_mentions=discord.AllowedMentions(users=[discord.Object(id=user_id)]))
                    return
                except discord.HTTPException:
                    pass
        try:
            user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)
            await user.send(f"⏰ Reminder: {row['message']}")
        except discord.HTTPException:
            log.info("Could not deliver reminder %s", row["id"])

    # ======================================================================
    # Temporary study rooms
    # ======================================================================
    @commands.Cog.listener()
    async def on_ready(self) -> None:
        for row in await self.bot.db.fetchall("SELECT channel_id FROM temp_rooms"):
            channel = self.bot.get_channel(row["channel_id"])
            if channel is None:
                await self.bot.db.execute("DELETE FROM temp_rooms WHERE channel_id=?", (row["channel_id"],))
            else:
                await self._maybe_delete_room(channel)

    @commands.Cog.listener()
    async def on_voice_state_update(
        self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState
    ) -> None:
        if member.bot:
            return
        if after.channel is not None and after.channel != before.channel:
            settings = await self.bot.db.get_settings(member.guild.id)
            if settings["lobby_channel"] and after.channel.id == settings["lobby_channel"]:
                await self._create_room(member, after.channel)
        if before.channel is not None and before.channel != after.channel:
            await self._maybe_delete_room(before.channel)

    async def _create_room(self, member: discord.Member, lobby: discord.VoiceChannel) -> None:
        now = time.monotonic()
        if now - self._last_create.get(member.id, 0.0) < ROOM_COOLDOWN:
            try:
                await member.move_to(None, reason="Room creation cooldown")
            except discord.HTTPException:
                pass
            return
        self._last_create[member.id] = now

        overwrites = dict(lobby.overwrites)
        overwrites[member] = discord.PermissionOverwrite(
            view_channel=True, connect=True, manage_channels=True, move_members=True
        )
        try:
            room = await member.guild.create_voice_channel(
                name=utils.truncate(f"📚 {member.display_name}'s room", 100),
                category=lobby.category,
                overwrites=overwrites,
                reason=f"Temporary study room for {member}",
            )
        except discord.HTTPException:
            log.warning("Could not create study room in guild %s (missing permissions?)", member.guild.id)
            return
        await self.bot.db.execute(
            "INSERT OR REPLACE INTO temp_rooms (channel_id, guild_id, owner_id) VALUES (?,?,?)",
            (room.id, member.guild.id, member.id),
        )
        try:
            await member.move_to(room, reason="Moved to their study room")
        except discord.HTTPException:
            await self._delete_room(room)

    async def _delete_room(self, channel) -> None:
        await self.bot.db.execute("DELETE FROM temp_rooms WHERE channel_id=?", (channel.id,))
        try:
            await channel.delete(reason="Temporary study room is empty")
        except discord.HTTPException:
            pass

    async def _maybe_delete_room(self, channel) -> None:
        if not isinstance(channel, (discord.VoiceChannel, discord.StageChannel)) or channel.members:
            return
        if await self.bot.db.scalar("SELECT 1 FROM temp_rooms WHERE channel_id=?", (channel.id,)):
            await self._delete_room(channel)

    async def _owned_room(self, interaction: discord.Interaction) -> discord.VoiceChannel | None:
        voice = interaction.user.voice  # type: ignore[union-attr]
        if voice is None or voice.channel is None:
            await interaction.response.send_message("Join your study room first, then run this command.", ephemeral=True)
            return None
        row = await self.bot.db.fetchone("SELECT owner_id FROM temp_rooms WHERE channel_id=?", (voice.channel.id,))
        if row is None:
            await interaction.response.send_message("You're not in a temporary study room.", ephemeral=True)
            return None
        if row["owner_id"] != interaction.user.id and not interaction.permissions.manage_channels:
            await interaction.response.send_message("Only the room's owner can do that.", ephemeral=True)
            return None
        return voice.channel  # type: ignore[return-value]

    @room.command(name="lock", description="Only invited people can join your room")
    async def room_lock(self, interaction: discord.Interaction) -> None:
        channel = await self._owned_room(interaction)
        if channel is None:
            return
        await channel.set_permissions(interaction.guild.default_role, connect=False, reason="Room locked")
        await interaction.response.send_message("🔒 Room locked. Use `/room invite` to let someone in.", ephemeral=True)

    @room.command(name="unlock", description="Let anyone join your room again")
    async def room_unlock(self, interaction: discord.Interaction) -> None:
        channel = await self._owned_room(interaction)
        if channel is None:
            return
        await channel.set_permissions(interaction.guild.default_role, connect=None, reason="Room unlocked")
        await interaction.response.send_message("🔓 Room unlocked.", ephemeral=True)

    @room.command(name="invite", description="Let a specific person join your room")
    async def room_invite(self, interaction: discord.Interaction, member: discord.Member) -> None:
        channel = await self._owned_room(interaction)
        if channel is None:
            return
        await channel.set_permissions(member, connect=True, view_channel=True, reason="Invited to study room")
        await interaction.response.send_message(f"✅ {member.mention} can now join **{channel.name}**.", ephemeral=True)

    @room.command(name="rename", description="Rename your room")
    async def room_rename(self, interaction: discord.Interaction, name: app_commands.Range[str, 1, 60]) -> None:
        channel = await self._owned_room(interaction)
        if channel is None:
            return
        await interaction.response.defer(ephemeral=True)  # Discord rate-limits channel renames, so respond first
        await channel.edit(name=f"📚 {name}", reason="Room renamed")
        await interaction.followup.send("✏️ Renamed.", ephemeral=True)

    @room.command(name="limit", description="Set the maximum number of people (0 = unlimited)")
    async def room_limit(self, interaction: discord.Interaction, people: app_commands.Range[int, 0, 99]) -> None:
        channel = await self._owned_room(interaction)
        if channel is None:
            return
        await channel.edit(user_limit=people, reason="Room limit changed")
        await interaction.response.send_message(f"👥 Limit set to {people or 'unlimited'}.", ephemeral=True)

    # ======================================================================
    # Quiet hours
    # ======================================================================
    @quiet.command(name="on", description="Temporarily stop people chatting in a channel")
    @app_commands.checks.has_permissions(manage_channels=True)
    @app_commands.describe(duration="How long: 30m, 2h (max 24h)", channel="Channel to quiet (default: this one)")
    async def quiet_on(
        self, interaction: discord.Interaction, duration: str = "1h", channel: discord.TextChannel | None = None
    ) -> None:
        target = channel or interaction.channel
        if not isinstance(target, discord.TextChannel):
            await interaction.response.send_message("Pick a regular text channel.", ephemeral=True)
            return
        seconds = utils.parse_duration(duration)
        if seconds is None or not 60 <= seconds <= 86400:
            await interaction.response.send_message("Duration must be between 1 minute and 24 hours, e.g. `2h`.", ephemeral=True)
            return
        role = interaction.guild.default_role
        overwrite = target.overwrites_for(role)
        existing = await self.bot.db.fetchone("SELECT prev FROM quiet_channels WHERE channel_id=?", (target.id,))
        prev = existing["prev"] if existing else (-1 if overwrite.send_messages is None else int(overwrite.send_messages))
        overwrite.send_messages = False
        try:
            await target.set_permissions(role, overwrite=overwrite, reason=f"Quiet hours by {interaction.user}")
        except discord.Forbidden:
            await interaction.response.send_message("I need the **Manage Channels** permission for that channel.", ephemeral=True)
            return
        unlock = int(time.time() + seconds)
        await self.bot.db.execute(
            "INSERT INTO quiet_channels (channel_id, guild_id, unlock_ts, prev) VALUES (?,?,?,?) "
            "ON CONFLICT(channel_id) DO UPDATE SET unlock_ts = excluded.unlock_ts",
            (target.id, interaction.guild_id, unlock, prev),
        )
        await interaction.response.send_message(f"🤫 Quiet hours on in {target.mention} until <t:{unlock}:t>.", ephemeral=True)
        await target.send(f"🤫 **Quiet hours** - this channel is paused until <t:{unlock}:t> (<t:{unlock}:R>). Deep work time!")

    @quiet.command(name="off", description="End quiet hours early")
    @app_commands.checks.has_permissions(manage_channels=True)
    async def quiet_off(self, interaction: discord.Interaction, channel: discord.TextChannel | None = None) -> None:
        target = channel or interaction.channel
        row = await self.bot.db.fetchone("SELECT prev FROM quiet_channels WHERE channel_id=?", (getattr(target, "id", 0),))
        if row is None:
            await interaction.response.send_message("Quiet hours aren't active there.", ephemeral=True)
            return
        await interaction.response.send_message("🔓 Quiet hours ended.", ephemeral=True)
        await self._end_quiet(target.id, row["prev"])

    async def _end_quiet(self, channel_id: int, prev: int) -> None:
        await self.bot.db.execute("DELETE FROM quiet_channels WHERE channel_id=?", (channel_id,))
        channel = self.bot.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        role = channel.guild.default_role
        overwrite = channel.overwrites_for(role)
        overwrite.send_messages = None if prev == -1 else bool(prev)
        try:
            await channel.set_permissions(role, overwrite=None if overwrite.is_empty() else overwrite, reason="Quiet hours ended")
            await channel.send("🔓 Quiet hours are over - chat is open again.")
        except discord.HTTPException:
            log.warning("Could not restore permissions in channel %s", channel_id)

    @tasks.loop(seconds=30)
    async def quiet_loop(self) -> None:
        rows = await self.bot.db.fetchall("SELECT channel_id, prev FROM quiet_channels WHERE unlock_ts <= ?", (int(time.time()),))
        for row in rows:
            await self._end_quiet(row["channel_id"], row["prev"])

    @quiet_loop.before_loop
    async def _before_quiet(self) -> None:
        await self.bot.wait_until_ready()

    # ======================================================================
    # Ambient sounds, poll, daily thread
    # ======================================================================
    @app_commands.command(name="ambient", description="Focus music and ambient sound links")
    @app_commands.choices(kind=[app_commands.Choice(name=v[0], value=k) for k, v in AMBIENT.items()])
    async def ambient(self, interaction: discord.Interaction, kind: app_commands.Choice[str]) -> None:
        label, links = AMBIENT[kind.value]
        lines = [f"• [{title}]({url})" for title, url in links]
        await interaction.response.send_message(embed=discord.Embed(
            title=f"🎧 {label}", description="\n".join(lines), color=utils.PURPLE))

    @app_commands.command(name="poll", description="Create a quick poll")
    @app_commands.guild_only()
    @app_commands.describe(question="What are you asking?", options="2-9 choices separated by | (e.g. Pizza | Pasta | Salad)")
    async def poll(
        self, interaction: discord.Interaction, question: app_commands.Range[str, 1, 200], options: app_commands.Range[str, 1, 600]
    ) -> None:
        choices = [o.strip() for o in options.split("|") if o.strip()]
        if not 2 <= len(choices) <= 9:
            await interaction.response.send_message("Give me between 2 and 9 options separated by `|`.", ephemeral=True)
            return
        lines = [f"{NUMBER_EMOJI[i]} {utils.truncate(c, 100)}" for i, c in enumerate(choices)]
        embed = discord.Embed(title=f"📊 {question}", description="\n".join(lines), color=utils.BRAND)
        embed.set_footer(text=f"Poll by {interaction.user.display_name}")
        await interaction.response.send_message(embed=embed)
        message = await interaction.original_response()
        for i in range(len(choices)):
            try:
                await message.add_reaction(NUMBER_EMOJI[i])
            except discord.HTTPException:
                break

    @tasks.loop(minutes=1)
    async def daily_loop(self) -> None:
        if utils.now_local().hour != config.DAILY_HOUR:
            return
        today = utils.today_str()
        for guild in self.bot.guilds:
            settings = await self.bot.db.get_settings(guild.id)
            channel = guild.get_channel(settings["daily_channel"]) if settings["daily_channel"] else None
            if channel is None or not await self.bot.db.claim(f"daily:{guild.id}", today):
                continue
            embed = discord.Embed(
                title="🌅 What are you working on today?",
                description="Drop your plan for the day below - big or small. Set a goal with `/goal set` and start a session with `/focus start`.",
                color=utils.GREEN,
            )
            try:
                message = await channel.send(embed=embed)
                await message.create_thread(name=f"Today · {today}", auto_archive_duration=1440)
            except discord.HTTPException:
                log.warning("Could not post daily thread in guild %s", guild.id)

    @daily_loop.before_loop
    async def _before_daily(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Environment(bot))
