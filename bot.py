"""Entry point: python bot.py"""
from __future__ import annotations

import logging
import sys

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
from db import Database

log = logging.getLogger("bot")

COGS = [
    "cogs.focus",
    "cogs.tracking",
    "cogs.accountability",
    "cogs.tech",
    "cogs.environment",
    "cogs.admin",
    "cogs.levels",
    "cogs.pomodoro",
    "cogs.flashcards",
    "cogs.dev",
    "cogs.panel",
    "cogs.wizard",
]


class ProductivityBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True          # privileged: welcome flow, roles, DMs
        intents.message_content = True  # privileged: auto-moderation
        super().__init__(
            command_prefix=commands.when_mentioned,
            intents=intents,
            help_command=None,
            allowed_mentions=discord.AllowedMentions(everyone=False, roles=False, users=True),
        )
        self.db = Database(config.DB_PATH)
        self.http_session: aiohttp.ClientSession | None = None

    async def setup_hook(self) -> None:
        await self.db.connect()
        self.http_session = aiohttp.ClientSession(headers={"User-Agent": "ProductivityBot/1.0"})
        self.tree.error(self.on_tree_error)

        for ext in COGS:
            await self.load_extension(ext)
            log.info("Loaded %s", ext)

        if config.SYNC_COMMANDS:
            if config.DEV_GUILD_ID:
                guild = discord.Object(id=config.DEV_GUILD_ID)
                self.tree.copy_global_to(guild=guild)
                synced = await self.tree.sync(guild=guild)
                log.info("Synced %d commands to dev guild %s", len(synced), config.DEV_GUILD_ID)
            else:
                synced = await self.tree.sync()
                log.info("Synced %d global commands", len(synced))

    async def on_ready(self) -> None:
        log.info("Logged in as %s (%s) in %d servers", self.user, self.user.id if self.user else "?", len(self.guilds))
        await self.change_presence(activity=discord.Activity(type=discord.ActivityType.watching, name="you focus · /help"))

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            message = f"Slow down - try again in {error.retry_after:.0f}s."
        elif isinstance(error, app_commands.MissingPermissions):
            message = "You don't have permission to use this command."
        elif isinstance(error, app_commands.NoPrivateMessage):
            message = "This command only works inside a server."
        else:
            log.exception("Unhandled command error", exc_info=error)
            message = "Something went wrong on my side. Please try again in a moment."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            pass

    async def close(self) -> None:
        await super().close()
        if self.http_session is not None:
            await self.http_session.close()
        await self.db.close()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        stream=sys.stdout,
    )
    if not config.TOKEN:
        sys.exit("DISCORD_TOKEN is not set. Copy .env.example to .env (or set it in Railway Variables).")
    ProductivityBot().run(config.TOKEN, log_handler=None)


if __name__ == "__main__":
    main()
