"""Developer tools: code runner (Piston API), GitHub lookup, PyPI lookup."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import datetime

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

import config
import utils

log = logging.getLogger(__name__)

LANG_ALIASES = {
    "py": "python", "python3": "python", "js": "javascript", "node": "javascript", "ts": "typescript",
    "c++": "c++", "cpp": "c++", "cc": "c++", "c#": "csharp", "cs": "csharp", "rb": "ruby", "rs": "rust",
    "golang": "go", "sh": "bash", "shell": "bash", "kt": "kotlin", "pl": "perl",
}
GITHUB_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}(/[A-Za-z0-9._-]{1,100})?$")
PYPI_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
RUN_COOLDOWN = 8.0
TIMEOUT = aiohttp.ClientTimeout(total=20)


def clean_output(text: str, limit: int = 1500) -> str:
    text = text.replace("```", "'''").strip("\n")
    return utils.truncate(text, limit) if text else "(no output)"


class RunModal(discord.ui.Modal, title="Run code"):
    language = discord.ui.TextInput(label="Language", max_length=20, placeholder="python, javascript, c++, go, rust, java ...")
    code = discord.ui.TextInput(label="Code", style=discord.TextStyle.paragraph, max_length=2000)

    def __init__(self, cog: "Dev", language: str = ""):
        super().__init__()
        self.cog = cog
        if language:
            self.language.default = language

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.run_code(interaction, self.language.value, self.code.value)


class Dev(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._last_run: dict[int, float] = {}

    # ---- code runner -----------------------------------------------------
    @app_commands.command(name="run", description="Run a small program (opens a form)")
    @app_commands.describe(language="Language to prefill, e.g. python")
    async def run(self, interaction: discord.Interaction, language: app_commands.Range[str, 1, 20] | None = None) -> None:
        if not config.PISTON_URL:
            await interaction.response.send_message("The code runner is turned off on this bot.", ephemeral=True)
            return
        await interaction.response.send_modal(RunModal(self, language or ""))

    async def run_code(self, interaction: discord.Interaction, language: str, code: str) -> None:
        if not config.PISTON_URL:
            await interaction.response.send_message("The code runner is turned off on this bot.", ephemeral=True)
            return
        now = time.monotonic()
        wait = self._last_run.get(interaction.user.id, 0.0) + RUN_COOLDOWN - now
        if wait > 0:
            await interaction.response.send_message(f"Easy there - try again in {wait:.0f}s.", ephemeral=True)
            return
        self._last_run[interaction.user.id] = now

        lang = language.strip().lower()
        lang = LANG_ALIASES.get(lang, lang)
        await interaction.response.defer(thinking=True)
        headers = {"Authorization": config.PISTON_API_KEY} if config.PISTON_API_KEY else {}
        payload = {"language": lang, "version": "*", "files": [{"content": code}]}
        try:
            async with self.bot.http_session.post(  # type: ignore[union-attr]
                f"{config.PISTON_URL}/execute", json=payload, headers=headers, timeout=TIMEOUT
            ) as resp:
                status = resp.status
                data = await resp.json(content_type=None) if status == 200 else {"message": (await resp.text())[:200]}
        except (aiohttp.ClientError, asyncio.TimeoutError):
            await interaction.followup.send("The code runner didn't answer in time. Try again in a moment.")
            return

        if status != 200:
            message = data.get("message", "") if isinstance(data, dict) else ""
            await interaction.followup.send(
                f"The code runner couldn't run that (HTTP {status}). {message}\n"
                "If this keeps happening for every language, the runner service may need a key or a different "
                "address - the bot owner can set `PISTON_URL` / `PISTON_API_KEY`."
            )
            return

        compile_stage = data.get("compile") or {}
        run_stage = data.get("run") or {}
        if compile_stage.get("code") not in (None, 0):
            output, code_num, title = compile_stage.get("output", ""), compile_stage.get("code"), "Compile error"
        else:
            output = run_stage.get("output") or (run_stage.get("stdout", "") + run_stage.get("stderr", ""))
            code_num, title = run_stage.get("code", 0), "Output"
        ok = code_num == 0
        embed = discord.Embed(
            title=f"{'✅' if ok else '❌'} {title} · {data.get('language', lang)}",
            description=f"```\n{clean_output(output)}\n```",
            color=utils.GREEN if ok else utils.RED,
        )
        embed.set_footer(text=f"Run by {interaction.user.display_name} · exit code {code_num}")
        await interaction.followup.send(embed=embed)

    # ---- GitHub ----------------------------------------------------------
    async def _get_json(self, url: str, headers: dict | None = None):
        async with self.bot.http_session.get(url, headers=headers, timeout=TIMEOUT) as resp:  # type: ignore[union-attr]
            if resp.status != 200:
                return resp.status, None
            return 200, await resp.json(content_type=None)

    async def github_embed(self, query: str) -> discord.Embed | str:
        query = query.strip().strip("/")
        if "github.com/" in query:
            query = query.split("github.com/", 1)[1].strip("/")
        if not GITHUB_RE.match(query):
            return "Use a username like `torvalds` or a repo like `python/cpython`."
        headers = {"Accept": "application/vnd.github+json"}
        if config.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
        try:
            if "/" in query:
                status, repo = await self._get_json(f"https://api.github.com/repos/{query}", headers)
                if status == 404:
                    return f"I couldn't find the repository `{query}`."
                if repo is None:
                    return "GitHub didn't respond (rate limit?). Try again in a minute."
                embed = discord.Embed(
                    title=repo["full_name"], url=repo["html_url"],
                    description=utils.truncate(repo.get("description") or "No description", 300), color=utils.BRAND,
                )
                embed.add_field(name="⭐ Stars", value=f"{repo['stargazers_count']:,}")
                embed.add_field(name="🍴 Forks", value=f"{repo['forks_count']:,}")
                embed.add_field(name="🐞 Open issues", value=f"{repo['open_issues_count']:,}")
                embed.add_field(name="Language", value=repo.get("language") or "-")
                embed.add_field(name="License", value=(repo.get("license") or {}).get("spdx_id") or "-")
                pushed = repo.get("pushed_at")
                if pushed:
                    embed.add_field(name="Last push", value=f"<t:{int(datetime.fromisoformat(pushed.replace('Z', '+00:00')).timestamp())}:R>")
                topics = repo.get("topics") or []
                if topics:
                    embed.add_field(name="Topics", value=", ".join(topics[:8]), inline=False)
                embed.set_thumbnail(url=repo["owner"]["avatar_url"])
                return embed

            status, user = await self._get_json(f"https://api.github.com/users/{query}", headers)
            if status == 404:
                return f"I couldn't find the GitHub user `{query}`."
            if user is None:
                return "GitHub didn't respond (rate limit?). Try again in a minute."
            embed = discord.Embed(
                title=user.get("name") or user["login"], url=user["html_url"],
                description=utils.truncate(user.get("bio") or "", 300) or None, color=utils.BRAND,
            )
            embed.set_thumbnail(url=user["avatar_url"])
            embed.add_field(name="Repos", value=f"{user['public_repos']:,}")
            embed.add_field(name="Followers", value=f"{user['followers']:,}")
            embed.add_field(name="Following", value=f"{user['following']:,}")
            _, repos = await self._get_json(
                f"https://api.github.com/users/{query}/repos?per_page=100&sort=pushed", headers
            )
            if repos:
                top = sorted((r for r in repos if not r.get("fork")), key=lambda r: r["stargazers_count"], reverse=True)[:3]
                if top:
                    embed.add_field(
                        name="Top repositories",
                        value="\n".join(f"[{r['name']}]({r['html_url']}) ⭐ {r['stargazers_count']:,}" for r in top),
                        inline=False,
                    )
            return embed
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return "Couldn't reach GitHub right now."

    @app_commands.command(name="github", description="Look up a GitHub user or repository")
    @app_commands.describe(query="A username (torvalds) or a repo (python/cpython)")
    async def github(self, interaction: discord.Interaction, query: app_commands.Range[str, 1, 120]) -> None:
        await interaction.response.defer()
        result = await self.github_embed(query)
        if isinstance(result, str):
            await interaction.followup.send(result)
        else:
            await interaction.followup.send(embed=result)

    # ---- PyPI ------------------------------------------------------------
    async def pypi_embed(self, name: str) -> discord.Embed | str:
        name = name.strip()
        if not PYPI_RE.match(name):
            return "Package names only contain letters, numbers, `.`, `-` and `_`."
        try:
            status, data = await self._get_json(f"https://pypi.org/pypi/{name}/json")
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return "Couldn't reach PyPI right now."
        if status == 404:
            return f"No package called `{name}` on PyPI."
        if data is None:
            return "PyPI didn't respond. Try again in a minute."
        info = data["info"]
        links = info.get("project_urls") or {}
        home = info.get("home_page") or links.get("Homepage") or next(iter(links.values()), None)
        embed = discord.Embed(
            title=f"{info['name']} {info['version']}", url=info.get("package_url"),
            description=utils.truncate(info.get("summary") or "No description", 300), color=utils.PURPLE,
        )
        embed.add_field(name="Install", value=f"```\npip install {info['name']}\n```", inline=False)
        embed.add_field(name="Python", value=info.get("requires_python") or "-")
        embed.add_field(name="Author", value=utils.truncate(info.get("author") or info.get("author_email") or "-", 60))
        uploads = data.get("urls") or []
        if uploads and uploads[0].get("upload_time_iso_8601"):
            stamp = uploads[0]["upload_time_iso_8601"].replace("Z", "+00:00")
            embed.add_field(name="Released", value=f"<t:{int(datetime.fromisoformat(stamp).timestamp())}:R>")
        if home:
            embed.add_field(name="Homepage", value=utils.truncate(home, 200), inline=False)
        return embed

    @app_commands.command(name="pypi", description="Look up a Python package on PyPI")
    @app_commands.describe(package="Package name, e.g. requests")
    async def pypi(self, interaction: discord.Interaction, package: app_commands.Range[str, 1, 100]) -> None:
        await interaction.response.defer()
        result = await self.pypi_embed(package)
        if isinstance(result, str):
            await interaction.followup.send(result)
        else:
            await interaction.followup.send(embed=result)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Dev(bot))
