"""Tech tools: snippets, docs lookup, AI error helper, daily challenge, tech news, trending repos,
resources, and code-review threads."""
from __future__ import annotations

import asyncio
import logging
import re
import time
from datetime import timedelta
from urllib.parse import quote_plus

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import challenges
import config
import utils

log = logging.getLogger(__name__)

# ---- docs lookup ------------------------------------------------------------
# key -> (label, URL template with {q}). DuckDuckGo "site:" searches are used where a site has no stable search URL.
DOCS = {
    "python": ("Python", "https://docs.python.org/3/search.html?q={q}"),
    "javascript": ("JavaScript (MDN)", "https://developer.mozilla.org/en-US/search?q={q}"),
    "html": ("HTML (MDN)", "https://developer.mozilla.org/en-US/search?q={q}"),
    "css": ("CSS (MDN)", "https://developer.mozilla.org/en-US/search?q={q}"),
    "typescript": ("TypeScript", "https://duckduckgo.com/?q=site%3Atypescriptlang.org+{q}"),
    "react": ("React", "https://duckduckgo.com/?q=site%3Areact.dev+{q}"),
    "node": ("Node.js", "https://duckduckgo.com/?q=site%3Anodejs.org+{q}"),
    "rust": ("Rust std", "https://doc.rust-lang.org/std/?search={q}"),
    "go": ("Go packages", "https://pkg.go.dev/search?q={q}"),
    "java": ("Java", "https://duckduckgo.com/?q=site%3Adocs.oracle.com+java+{q}"),
    "cpp": ("C++ (cppreference)", "https://en.cppreference.com/mwiki/index.php?search={q}"),
    "sql": ("PostgreSQL", "https://www.postgresql.org/search/?q={q}"),
    "git": ("Git", "https://git-scm.com/search/results?search={q}"),
    "docker": ("Docker", "https://docs.docker.com/search/?q={q}"),
    "pypi": ("PyPI packages", "https://pypi.org/search/?q={q}"),
    "npm": ("npm packages", "https://www.npmjs.com/search?q={q}"),
}

# ---- learning resources -----------------------------------------------------
RESOURCES = {
    "web": ("Web development", [
        ("Frontend roadmap", "https://roadmap.sh/frontend"),
        ("Backend roadmap", "https://roadmap.sh/backend"),
        ("The Odin Project (free full-stack course)", "https://www.theodinproject.com/"),
        ("freeCodeCamp", "https://www.freecodecamp.org/"),
        ("MDN Web Docs", "https://developer.mozilla.org/"),
    ]),
    "python": ("Python", [
        ("Python roadmap", "https://roadmap.sh/python"),
        ("Official Python tutorial", "https://docs.python.org/3/tutorial/"),
        ("CS50P (Harvard, free)", "https://cs50.harvard.edu/python/"),
        ("Real Python", "https://realpython.com/"),
    ]),
    "dsa": ("Data structures & algorithms", [
        ("DSA roadmap", "https://roadmap.sh/datastructures-and-algorithms"),
        ("NeetCode roadmap", "https://neetcode.io/roadmap"),
        ("CS50 (Harvard, free)", "https://cs50.harvard.edu/x/"),
        ("VisuAlgo (visual explanations)", "https://visualgo.net/"),
    ]),
    "ai": ("AI / machine learning", [
        ("AI & data science roadmap", "https://roadmap.sh/ai-data-scientist"),
        ("fast.ai practical course", "https://course.fast.ai/"),
        ("Kaggle Learn (free micro-courses)", "https://www.kaggle.com/learn"),
    ]),
    "cyber": ("Cybersecurity", [
        ("Cyber security roadmap", "https://roadmap.sh/cyber-security"),
        ("TryHackMe", "https://tryhackme.com/"),
        ("OverTheWire wargames", "https://overthewire.org/wargames/"),
        ("PortSwigger Web Security Academy", "https://portswigger.net/web-security"),
    ]),
    "devops": ("DevOps", [
        ("DevOps roadmap", "https://roadmap.sh/devops"),
        ("Docker docs", "https://docs.docker.com/get-started/"),
        ("Kubernetes basics", "https://kubernetes.io/docs/tutorials/kubernetes-basics/"),
    ]),
    "mobile": ("Mobile development", [
        ("Android roadmap", "https://roadmap.sh/android"),
        ("Flutter docs", "https://docs.flutter.dev/"),
        ("React Native docs", "https://reactnative.dev/docs/getting-started"),
    ]),
}

SYSTEM_PROMPT = (
    "You are a concise, friendly senior engineer helping students and developers in a Discord server. "
    "Explain the likely cause of the error or code problem in plain language, then give the most likely fix "
    "with a short code example if useful. Keep the whole answer under 350 words. If you are unsure, say what "
    "extra information would help. Never invent library functions."
)

INVITE_SAFE = re.compile(r"[^a-z0-9+#.\-]")


def safe_lang(language: str | None) -> str:
    return INVITE_SAFE.sub("", (language or "").lower())[:15]


def clean_name(name: str) -> str:
    return re.sub(r"[^a-z0-9_\-]", "", name.strip().lower().replace(" ", "-"))[:40]


def fence(code: str, language: str = "") -> str:
    return f"```{language}\n{code.replace('```', '``' + chr(0x200b) + '`')}\n```"


class SnippetModal(discord.ui.Modal, title="Save a code snippet"):
    name = discord.ui.TextInput(label="Name", max_length=40, placeholder="e.g. fastapi-hello")
    language = discord.ui.TextInput(label="Language", max_length=15, required=False, placeholder="python")
    code = discord.ui.TextInput(label="Code", style=discord.TextStyle.paragraph, max_length=1500)

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction) -> None:
        name = clean_name(self.name.value)
        if not name:
            await interaction.response.send_message("Please use letters, numbers, `-` or `_` in the name.", ephemeral=True)
            return
        res = await self.bot.db.execute(
            "INSERT INTO snippets (guild_id, name, user_id, language, code, created_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(guild_id, name) DO UPDATE SET language=excluded.language, code=excluded.code "
            "WHERE snippets.user_id = excluded.user_id",
            (interaction.guild_id, name, interaction.user.id, safe_lang(self.language.value),
             self.code.value, utils.now_local().isoformat(timespec="seconds")),
        )
        if res.rowcount:
            await interaction.response.send_message(f"💾 Saved snippet **{name}**. Get it with `/snippet get {name}`.", ephemeral=True)
        else:
            await interaction.response.send_message(f"**{name}** already belongs to someone else. Pick another name.", ephemeral=True)


class Tech(commands.Cog):
    snippet = app_commands.Group(name="snippet", description="Save and share code snippets", guild_only=True)
    challenge = app_commands.Group(name="challenge", description="Daily coding challenge", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.ai_last: dict[int, float] = {}

    async def cog_load(self) -> None:
        self.news_loop.start()
        self.challenge_loop.start()

    async def cog_unload(self) -> None:
        self.news_loop.cancel()
        self.challenge_loop.cancel()

    # ---- snippets --------------------------------------------------------
    async def _snippet_names(self, interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
        rows = await self.bot.db.fetchall(
            "SELECT name FROM snippets WHERE guild_id=? AND name LIKE ? ORDER BY name LIMIT 25",
            (interaction.guild_id, f"{clean_name(current)}%"),
        )
        return [app_commands.Choice(name=r["name"], value=r["name"]) for r in rows]

    @snippet.command(name="save", description="Save a code snippet (opens a form)")
    async def snippet_save(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SnippetModal(self.bot))

    @snippet.command(name="get", description="Post a saved snippet")
    @app_commands.autocomplete(name=_snippet_names)
    async def snippet_get(self, interaction: discord.Interaction, name: str) -> None:
        row = await self.bot.db.fetchone(
            "SELECT * FROM snippets WHERE guild_id=? AND name=?", (interaction.guild_id, clean_name(name))
        )
        if row is None:
            await interaction.response.send_message("No snippet with that name. Try `/snippet list`.", ephemeral=True)
            return
        header = f"**{row['name']}** · by <@{row['user_id']}>"
        await interaction.response.send_message(f"{header}\n{fence(row['code'], row['language'] or '')}")

    @snippet.command(name="list", description="List saved snippets")
    async def snippet_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(
            "SELECT name, language, user_id FROM snippets WHERE guild_id=? ORDER BY name LIMIT 50", (interaction.guild_id,)
        )
        if not rows:
            await interaction.response.send_message("No snippets yet. Add one with `/snippet save`.", ephemeral=True)
            return
        lines = [f"`{r['name']}` {('· ' + r['language']) if r['language'] else ''} · <@{r['user_id']}>" for r in rows]
        await interaction.response.send_message(embed=discord.Embed(
            title="💾 Snippets", description=utils.truncate("\n".join(lines), 4000), color=utils.BRAND), ephemeral=True)

    @snippet.command(name="delete", description="Delete one of your snippets")
    @app_commands.autocomplete(name=_snippet_names)
    async def snippet_delete(self, interaction: discord.Interaction, name: str) -> None:
        name = clean_name(name)
        if interaction.permissions.manage_messages:
            res = await self.bot.db.execute("DELETE FROM snippets WHERE guild_id=? AND name=?", (interaction.guild_id, name))
        else:
            res = await self.bot.db.execute(
                "DELETE FROM snippets WHERE guild_id=? AND name=? AND user_id=?", (interaction.guild_id, name, interaction.user.id)
            )
        await interaction.response.send_message("🗑️ Deleted." if res.rowcount else "Nothing deleted (not found, or not yours).", ephemeral=True)

    # ---- docs ------------------------------------------------------------
    @app_commands.command(name="docs", description="Jump to documentation for a language or tool")
    @app_commands.describe(language="Which docs to search", query="What to look up, e.g. 'list.sort'")
    @app_commands.choices(language=[app_commands.Choice(name=v[0], value=k) for k, v in DOCS.items()])
    async def docs(self, interaction: discord.Interaction, language: app_commands.Choice[str], query: app_commands.Range[str, 1, 100]) -> None:
        label, template = DOCS[language.value]
        q = quote_plus(query)
        so = f"https://stackoverflow.com/search?q={quote_plus(language.value + ' ' + query)}"
        embed = discord.Embed(
            title=f"📚 {label}: {query}",
            description=f"[Search the {label} docs]({template.format(q=q)})\n[Search Stack Overflow]({so})",
            color=utils.BRAND,
        )
        await interaction.response.send_message(embed=embed)

    # ---- AI error helper -------------------------------------------------
    @app_commands.command(name="explain", description="Paste an error or code and get help understanding it")
    @app_commands.checks.cooldown(1, 20.0, key=lambda i: i.user.id)
    async def explain(self, interaction: discord.Interaction) -> None:
        if not config.AI_ENABLED:
            await interaction.response.send_message(
                "The AI helper isn't configured on this bot. The owner can enable it by setting "
                "`AI_API_KEY`, `AI_BASE_URL` and `AI_MODEL`.", ephemeral=True)
            return
        await interaction.response.send_modal(ExplainModal(self))

    def ai_cooldown(self, user_id: int, seconds: float = 15.0) -> float:
        """Seconds the user still has to wait before another AI request (0 = allowed, and starts the cooldown)."""
        now = time.monotonic()
        wait = self.ai_last.get(user_id, 0.0) + seconds - now
        if wait > 0:
            return wait
        self.ai_last[user_id] = now
        return 0.0

    async def ask_ai(
        self, prompt: str, system: str = SYSTEM_PROMPT, max_tokens: int = 700, temperature: float = 0.2
    ) -> str:
        assert self.bot.http_session is not None  # type: ignore[attr-defined]
        payload = {
            "model": config.AI_MODEL,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        headers = {"Authorization": f"Bearer {config.AI_API_KEY}"}
        url = f"{config.AI_BASE_URL.rstrip('/')}/chat/completions"
        async with self.bot.http_session.post(  # type: ignore[attr-defined]
            url, json=payload, headers=headers, timeout=aiohttp.ClientTimeout(total=45)
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                raise RuntimeError(f"AI API returned {resp.status}: {body}")
            data = await resp.json()
        return data["choices"][0]["message"]["content"].strip()

    # ---- daily challenge -------------------------------------------------
    def _challenge_embed(self) -> discord.Embed:
        c = challenges.challenge_for(utils.today_local())
        embed = discord.Embed(
            title=f"🧩 Daily challenge: {c['title']}",
            url=c["url"],
            description=f"{c['summary']}\n\n{c['emoji']} **{c['difficulty']}** · {c['topics']}\n[Open the problem]({c['url']})",
            color=utils.GREEN,
        )
        embed.set_footer(text="Discuss in the thread. Wrap solutions in ||spoiler tags|| so others can still try!")
        return embed

    @challenge.command(name="today", description="Show today's coding challenge")
    async def challenge_today(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=self._challenge_embed())

    @tasks.loop(minutes=1)
    async def challenge_loop(self) -> None:
        if utils.now_local().hour != config.CHALLENGE_HOUR:
            return
        today = utils.today_str()
        for guild in self.bot.guilds:
            settings = await self.bot.db.get_settings(guild.id)
            channel = guild.get_channel(settings["challenge_channel"]) if settings["challenge_channel"] else None
            if channel is None or not await self.bot.db.claim(f"challenge:{guild.id}", today):
                continue
            try:
                message = await channel.send(embed=self._challenge_embed())
                await message.create_thread(name=f"Discuss · {today}", auto_archive_duration=1440)
            except discord.HTTPException:
                log.warning("Could not post challenge in guild %s", guild.id)

    @challenge_loop.before_loop
    async def _before_challenge(self) -> None:
        await self.bot.wait_until_ready()

    # ---- tech news -------------------------------------------------------
    async def _hn_embed(self, count: int = 5) -> discord.Embed | None:
        session = self.bot.http_session
        assert session is not None  # type: ignore[attr-defined]
        timeout = aiohttp.ClientTimeout(total=15)
        base = "https://hacker-news.firebaseio.com/v0"
        try:
            async with session.get(f"{base}/topstories.json", timeout=timeout) as resp:
                ids = (await resp.json())[: count + 5]

            async def fetch(story_id: int):
                async with session.get(f"{base}/item/{story_id}.json", timeout=timeout) as r:
                    return await r.json()

            items = [i for i in await asyncio.gather(*(fetch(i) for i in ids), return_exceptions=True)
                     if isinstance(i, dict) and i.get("title")][:count]
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None
        if not items:
            return None
        lines = []
        for item in items:
            link = item.get("url") or f"https://news.ycombinator.com/item?id={item['id']}"
            discuss = f"https://news.ycombinator.com/item?id={item['id']}"
            lines.append(f"• [{utils.truncate(item['title'], 90)}]({link})\n  {item.get('score', 0)} points · [{item.get('descendants', 0)} comments]({discuss})")
        return discord.Embed(title="📰 Top tech stories (Hacker News)", description="\n".join(lines), color=utils.ORANGE)

    @app_commands.command(name="news", description="Top tech stories right now")
    async def news(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        embed = await self._hn_embed()
        if embed is None:
            await interaction.followup.send("Couldn't reach Hacker News right now. Try again in a minute.")
        else:
            await interaction.followup.send(embed=embed)

    @tasks.loop(minutes=1)
    async def news_loop(self) -> None:
        if utils.now_local().hour != config.NEWS_HOUR:
            return
        today = utils.today_str()
        for guild in self.bot.guilds:
            settings = await self.bot.db.get_settings(guild.id)
            channel = guild.get_channel(settings["news_channel"]) if settings["news_channel"] else None
            if channel is None or not await self.bot.db.claim(f"news:{guild.id}", today):
                continue
            embed = await self._hn_embed()
            if embed is None:
                continue
            try:
                await channel.send(embed=embed)
            except discord.HTTPException:
                log.warning("Could not post news in guild %s", guild.id)

    @news_loop.before_loop
    async def _before_news(self) -> None:
        await self.bot.wait_until_ready()

    @app_commands.command(name="trending", description="New GitHub repos getting the most stars this week")
    @app_commands.describe(language="Optional language filter, e.g. python")
    async def trending(self, interaction: discord.Interaction, language: app_commands.Range[str, 1, 30] | None = None) -> None:
        await interaction.response.defer()
        result = await self.trending_result(language)
        if isinstance(result, str):
            await interaction.followup.send(result)
        else:
            await interaction.followup.send(embed=result)

    async def trending_result(self, language: str | None) -> discord.Embed | str:
        """An embed on success, otherwise a short error message."""
        since = (utils.today_local() - timedelta(days=7)).isoformat()
        q = f"created:>{since}" + (f" language:{safe_lang(language)}" if language else "")
        headers = {"Accept": "application/vnd.github+json"}
        if config.GITHUB_TOKEN:
            headers["Authorization"] = f"Bearer {config.GITHUB_TOKEN}"
        try:
            async with self.bot.http_session.get(  # type: ignore[union-attr]
                "https://api.github.com/search/repositories",
                params={"q": q, "sort": "stars", "order": "desc", "per_page": 5},
                headers=headers, timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    return "GitHub didn't respond (rate limit?). Try again in a minute."
                data = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return "Couldn't reach GitHub right now."
        repos = data.get("items", [])
        if not repos:
            return "No matching repositories found."
        lines = []
        for repo in repos:
            desc = utils.truncate(repo.get("description") or "No description", 100)
            lines.append(f"• [{repo['full_name']}]({repo['html_url']}) ⭐ {repo['stargazers_count']:,}\n  {desc}")
        embed = discord.Embed(title="🔭 Trending new repos (last 7 days)", description="\n".join(lines), color=utils.PURPLE)
        embed.set_footer(text="New repositories ranked by stars - an approximation of 'trending'.")
        return embed

    # ---- resources -------------------------------------------------------
    @app_commands.command(name="resources", description="Curated learning roadmaps and free courses")
    @app_commands.choices(topic=[app_commands.Choice(name=v[0], value=k) for k, v in RESOURCES.items()])
    async def resources(self, interaction: discord.Interaction, topic: app_commands.Choice[str]) -> None:
        label, links = RESOURCES[topic.value]
        lines = [f"• [{title}]({url})" for title, url in links]
        await interaction.response.send_message(embed=discord.Embed(
            title=f"🗺️ Learn: {label}", description="\n".join(lines), color=utils.GREEN))

    # ---- code review requests -------------------------------------------
    @app_commands.command(name="review", description="Ask for a code review in a new thread")
    @app_commands.guild_only()
    @app_commands.describe(title="What should people review?", link="GitHub/gist/playground link", details="What feedback do you want?")
    async def review(
        self,
        interaction: discord.Interaction,
        title: app_commands.Range[str, 3, 80],
        link: app_commands.Range[str, 1, 300] | None = None,
        details: app_commands.Range[str, 1, 500] | None = None,
    ) -> None:
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Use this in a regular text channel (not inside a thread).", ephemeral=True)
            return
        await interaction.response.send_message("Creating your review thread…", ephemeral=True)
        helper = discord.utils.get(interaction.guild.roles, name="Code Reviewer") or discord.utils.get(interaction.guild.roles, name="Helper")
        embed = discord.Embed(title=f"🔍 Review request: {title}", color=utils.BRAND)
        embed.set_author(name=interaction.user.display_name, icon_url=interaction.user.display_avatar.url)
        if link:
            embed.add_field(name="Link", value=link, inline=False)
        if details:
            embed.add_field(name="Feedback wanted", value=details, inline=False)
        ping = helper.mention if helper else ""
        try:
            message = await channel.send(
                ping, embed=embed, allowed_mentions=discord.AllowedMentions(roles=True, users=False)
            )
            thread = await message.create_thread(name=utils.truncate(f"Review · {title}", 90), auto_archive_duration=1440)
            await thread.send(f"{interaction.user.mention} put your code or questions here. Reviewers, be kind and specific! 🙌")
            await interaction.edit_original_response(content=f"Posted! Follow along in {thread.mention}.")
        except discord.HTTPException:
            await interaction.edit_original_response(content="I couldn't post here - check my permissions (Send Messages, Create Public Threads).")


class ExplainModal(discord.ui.Modal, title="Explain this error"):
    error = discord.ui.TextInput(
        label="Error message or code", style=discord.TextStyle.paragraph, max_length=3000,
        placeholder="Paste the traceback or the code that confuses you",
    )
    context = discord.ui.TextInput(
        label="Language / what you were trying to do", required=False, max_length=200, placeholder="Python, reading a CSV"
    )

    def __init__(self, cog: Tech):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        wait = self.cog.ai_cooldown(interaction.user.id)
        if wait:
            await interaction.response.send_message(f"Please wait {wait:.0f}s before asking the AI again.", ephemeral=True)
            return
        await interaction.response.defer(thinking=True)
        prompt = f"Context: {self.context.value or 'not given'}\n\nProblem:\n{self.error.value}"
        try:
            answer = await self.cog.ask_ai(prompt)
        except Exception:
            log.exception("AI request failed")
            await interaction.followup.send("The AI helper had a problem. Please try again in a bit.")
            return
        pieces = utils.chunk(answer, 1900)
        await interaction.followup.send(pieces[0])
        for piece in pieces[1:3]:
            await interaction.followup.send(piece)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tech(bot))
