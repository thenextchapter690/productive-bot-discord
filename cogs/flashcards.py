"""Spaced-repetition flashcards (Leitner boxes) and AI-generated quizzes."""
from __future__ import annotations

import json
import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

import config
import leveling
import utils

log = logging.getLogger(__name__)

# Leitner system: a correct answer moves a card up a box (longer wait), a miss sends it back to box 1.
INTERVALS = {1: 10 * 60, 2: 86400, 3: 3 * 86400, 4: 7 * 86400, 5: 21 * 86400}
MAX_CARDS = 500
LETTERS = "ABCD"

QUIZ_SYSTEM = (
    "You write multiple-choice quiz questions for learners. Reply with ONLY valid JSON - no markdown fences, no commentary."
)


def parse_quiz(raw: str) -> list[dict]:
    """Pull a list of valid questions out of a model reply (tolerates code fences and chatter)."""
    text = raw.strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        items = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return []
    questions = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        options, answer = item.get("options"), item.get("answer")
        if (
            isinstance(item.get("question"), str)
            and isinstance(options, list) and len(options) == 4 and all(isinstance(o, str) for o in options)
            and isinstance(answer, int) and not isinstance(answer, bool) and 0 <= answer <= 3
        ):
            questions.append({
                "question": utils.truncate(item["question"], 300),
                "options": [utils.truncate(o, 100) for o in options],
                "answer": answer,
                "explanation": utils.truncate(str(item.get("explanation", "")), 300),
            })
    return questions


# ---------------------------------------------------------------------------
class CardModal(discord.ui.Modal, title="New flashcard"):
    front = discord.ui.TextInput(label="Front (question or term)", style=discord.TextStyle.paragraph, max_length=300)
    back = discord.ui.TextInput(label="Back (answer)", style=discord.TextStyle.paragraph, max_length=600)

    def __init__(self, cog: "Flashcards"):
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: discord.Interaction) -> None:
        message = await self.cog.add_card(interaction.user.id, self.front.value.strip(), self.back.value.strip())
        await interaction.response.send_message(message, ephemeral=True)


class ReviewView(discord.ui.View):
    def __init__(self, cog: "Flashcards", user_id: int, card):
        super().__init__(timeout=900)
        self.cog, self.user_id, self.card, self.revealed = cog, user_id, card, False
        self._build()

    def _button(self, label: str, style: discord.ButtonStyle, emoji: str, callback) -> None:
        button = discord.ui.Button(label=label, style=style, emoji=emoji)
        button.callback = callback
        self.add_item(button)

    def _build(self) -> None:
        self.clear_items()
        if not self.revealed:
            self._button("Show answer", discord.ButtonStyle.primary, "👀", self.on_reveal)
        else:
            self._button("Got it", discord.ButtonStyle.success, "✅", self.on_good)
            self._button("Again", discord.ButtonStyle.danger, "❌", self.on_again)
        self._button("Stop", discord.ButtonStyle.secondary, "⏹️", self.on_stop)

    def embed(self) -> discord.Embed:
        embed = discord.Embed(title=f"🃏 Flashcard · box {self.card['box']}/5", color=utils.BRAND)
        embed.add_field(name="Question", value=self.card["front"], inline=False)
        if self.revealed:
            embed.add_field(name="Answer", value=self.card["back"], inline=False)
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def on_reveal(self, interaction: discord.Interaction) -> None:
        self.revealed = True
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def _rate(self, interaction: discord.Interaction, good: bool) -> None:
        await self.cog.rate(self.card, good)
        nxt = await self.cog.next_due(self.user_id)
        if nxt is None:
            await interaction.response.edit_message(embed=await self.cog.caught_up_embed(self.user_id), view=None)
            self.stop()
            return
        self.card, self.revealed = nxt, False
        self._build()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_good(self, interaction: discord.Interaction) -> None:
        await self._rate(interaction, True)

    async def on_again(self, interaction: discord.Interaction) -> None:
        await self._rate(interaction, False)

    async def on_stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(
            embed=discord.Embed(title="⏹️ Review stopped", description="Come back anytime - your progress is saved.", color=utils.BRAND),
            view=None,
        )
        self.stop()


class QuizView(discord.ui.View):
    def __init__(self, cog: "Flashcards", user_id: int, guild_id: int | None, topic: str, questions: list[dict]):
        super().__init__(timeout=900)
        self.cog, self.user_id, self.guild_id = cog, user_id, guild_id
        self.topic, self.questions = topic, questions
        self.index = 0
        self.score = 0
        self._build_question()

    def question_embed(self, feedback: str | None = None) -> discord.Embed:
        q = self.questions[self.index]
        options = "\n".join(f"**{LETTERS[i]}.** {opt}" for i, opt in enumerate(q["options"]))
        embed = discord.Embed(
            title=f"🧠 Quiz: {utils.truncate(self.topic, 60)} · {self.index + 1}/{len(self.questions)}",
            description=f"**{q['question']}**\n\n{options}",
            color=utils.BRAND,
        )
        if feedback:
            embed.add_field(name="Result", value=utils.truncate(feedback, 1000), inline=False)
        return embed

    def _build_question(self) -> None:
        self.clear_items()
        for i in range(4):
            button = discord.ui.Button(label=LETTERS[i], style=discord.ButtonStyle.secondary)
            button.callback = self._make_answer(i)
            self.add_item(button)

    def _build_next(self) -> None:
        self.clear_items()
        last = self.index + 1 >= len(self.questions)
        button = discord.ui.Button(label="See results" if last else "Next question", style=discord.ButtonStyle.primary)
        button.callback = self.on_next
        self.add_item(button)

    def _make_answer(self, choice: int):
        async def callback(interaction: discord.Interaction) -> None:
            await self.answer(interaction, choice)
        return callback

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.user_id

    async def answer(self, interaction: discord.Interaction, choice: int) -> None:
        q = self.questions[self.index]
        correct = choice == q["answer"]
        if correct:
            self.score += 1
        verdict = "✅ **Correct!**" if correct else f"❌ Not quite - the answer was **{LETTERS[q['answer']]}**."
        feedback = f"{verdict} {q['explanation']}".strip()
        self._build_next()
        await interaction.response.edit_message(embed=self.question_embed(feedback), view=self)
        if correct and self.guild_id:
            await leveling.grant(self.cog.bot, self.guild_id, self.user_id, leveling.XP_QUIZ_CORRECT, "quiz")

    async def on_next(self, interaction: discord.Interaction) -> None:
        self.index += 1
        if self.index >= len(self.questions):
            total = len(self.questions)
            embed = discord.Embed(
                title="🏁 Quiz finished",
                description=(
                    f"You scored **{self.score}/{total}** on *{utils.truncate(self.topic, 60)}*.\n"
                    f"+{self.score * leveling.XP_QUIZ_CORRECT} XP earned."
                ),
                color=utils.GREEN if self.score >= total / 2 else utils.ORANGE,
            )
            await interaction.response.edit_message(embed=embed, view=None)
            self.stop()
            return
        self._build_question()
        await interaction.response.edit_message(embed=self.question_embed(), view=self)


# ---------------------------------------------------------------------------
class Flashcards(commands.Cog):
    flashcards = app_commands.Group(name="flashcards", description="Spaced-repetition flashcards")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ---- data helpers ----------------------------------------------------
    async def add_card(self, user_id: int, front: str, back: str) -> str:
        if not front or not back:
            return "Both sides of the card need some text."
        count = await self.bot.db.scalar("SELECT COUNT(*) FROM cards WHERE user_id=?", (user_id,))
        if (count or 0) >= MAX_CARDS:
            return f"You've reached the limit of {MAX_CARDS} cards. Delete a few first."
        await self.bot.db.execute(
            "INSERT INTO cards (user_id, front, back, box, due_ts) VALUES (?,?,?,1,?)", (user_id, front, back, int(time.time()))
        )
        return f"🃏 Card saved! You now have **{(count or 0) + 1}** card(s). Review them with `/flashcards review`."

    async def counts(self, user_id: int) -> tuple[int, int]:
        total = await self.bot.db.scalar("SELECT COUNT(*) FROM cards WHERE user_id=?", (user_id,))
        due = await self.bot.db.scalar("SELECT COUNT(*) FROM cards WHERE user_id=? AND due_ts<=?", (user_id, int(time.time())))
        return int(total or 0), int(due or 0)

    async def next_due(self, user_id: int):
        return await self.bot.db.fetchone(
            "SELECT * FROM cards WHERE user_id=? AND due_ts<=? ORDER BY due_ts LIMIT 1", (user_id, int(time.time()))
        )

    async def rate(self, card, good: bool) -> None:
        box = min(card["box"] + 1, 5) if good else 1
        await self.bot.db.execute(
            "UPDATE cards SET box=?, due_ts=? WHERE id=?", (box, int(time.time()) + INTERVALS[box], card["id"])
        )

    async def caught_up_embed(self, user_id: int) -> discord.Embed:
        total, _ = await self.counts(user_id)
        nxt = await self.bot.db.scalar("SELECT MIN(due_ts) FROM cards WHERE user_id=?", (user_id,))
        if not total:
            text = "You don't have any cards yet. Add one with **Add card** or `/flashcards add`."
        else:
            text = f"🎉 All caught up! Next card is due <t:{int(nxt)}:R>." if nxt else "🎉 All caught up!"
        return discord.Embed(title="🃏 Flashcards", description=text, color=utils.GREEN)

    async def review_start(self, user_id: int) -> tuple[discord.Embed, discord.ui.View | None]:
        card = await self.next_due(user_id)
        if card is None:
            return await self.caught_up_embed(user_id), None
        view = ReviewView(self, user_id, card)
        return view.embed(), view

    # ---- quiz ------------------------------------------------------------
    async def start_quiz(self, interaction: discord.Interaction, topic: str, count: int = 5, difficulty: str = "medium") -> None:
        """Works from a slash command or a modal submit: replies privately to the asker."""
        tech = self.bot.get_cog("Tech")
        if not config.AI_ENABLED or tech is None:
            await interaction.response.send_message(
                "AI quizzes aren't enabled on this bot. The owner can switch them on by setting `AI_API_KEY`, "
                "`AI_BASE_URL` and `AI_MODEL`. Flashcards still work without AI!", ephemeral=True)
            return
        wait = tech.ai_cooldown(interaction.user.id)
        if wait:
            await interaction.response.send_message(f"Please wait {wait:.0f}s before asking the AI again.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        prompt = (
            f"Write {count} {difficulty}-level multiple-choice questions about: {topic}\n"
            'Return a JSON array. Each item: {"question": string, "options": [4 short strings], '
            '"answer": index 0-3 of the correct option, "explanation": one short sentence}. '
            "Exactly one option must be correct. Do not repeat questions."
        )
        try:
            raw = await tech.ask_ai(prompt, system=QUIZ_SYSTEM, max_tokens=1800, temperature=0.4)
        except Exception:
            log.exception("Quiz generation failed")
            await interaction.followup.send("The AI helper had a problem. Please try again in a bit.", ephemeral=True)
            return
        questions = parse_quiz(raw)[:count]
        if not questions:
            await interaction.followup.send("I couldn't build a quiz from the AI's reply. Try again or use a clearer topic.", ephemeral=True)
            return
        view = QuizView(self, interaction.user.id, interaction.guild_id, topic, questions)
        await interaction.followup.send(embed=view.question_embed(), view=view, ephemeral=True)

    @app_commands.command(name="quiz", description="Get an AI-written practice quiz on any topic")
    @app_commands.guild_only()
    @app_commands.describe(topic="e.g. Python decorators, TCP handshake, photosynthesis", count="Number of questions")
    @app_commands.choices(difficulty=[app_commands.Choice(name=n, value=n) for n in ("easy", "medium", "hard")])
    async def quiz(
        self,
        interaction: discord.Interaction,
        topic: app_commands.Range[str, 2, 100],
        count: app_commands.Range[int, 3, 8] = 5,
        difficulty: app_commands.Choice[str] | None = None,
    ) -> None:
        await self.start_quiz(interaction, topic, count, difficulty.value if difficulty else "medium")

    # ---- flashcard commands ---------------------------------------------
    @flashcards.command(name="add", description="Create a flashcard (opens a form)")
    async def cards_add(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(CardModal(self))

    @flashcards.command(name="review", description="Review the cards that are due now")
    async def cards_review(self, interaction: discord.Interaction) -> None:
        embed, view = await self.review_start(interaction.user.id)
        if view is None:
            await interaction.response.send_message(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @flashcards.command(name="list", description="See your deck and what's due")
    async def cards_list(self, interaction: discord.Interaction) -> None:
        rows = await self.bot.db.fetchall(
            "SELECT id, front, box, due_ts FROM cards WHERE user_id=? ORDER BY id DESC LIMIT 15", (interaction.user.id,)
        )
        total, due = await self.counts(interaction.user.id)
        if not total:
            await interaction.response.send_message("No cards yet. Create one with `/flashcards add`.", ephemeral=True)
            return
        boxes = await self.bot.db.fetchall("SELECT box, COUNT(*) AS n FROM cards WHERE user_id=? GROUP BY box ORDER BY box", (interaction.user.id,))
        box_line = "  ".join(f"box {b['box']}: **{b['n']}**" for b in boxes)
        lines = [f"`#{r['id']}` {utils.truncate(r['front'].replace(chr(10), ' '), 70)}  ·  box {r['box']}" for r in rows]
        embed = discord.Embed(title="🃏 Your flashcards", description="\n".join(lines), color=utils.BRAND)
        embed.add_field(name="Deck", value=f"{total} card(s) · **{due} due now**\n{box_line}", inline=False)
        embed.set_footer(text="Showing the 15 newest. Delete with /flashcards delete <id>.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @flashcards.command(name="delete", description="Delete a flashcard by its id")
    async def cards_delete(self, interaction: discord.Interaction, card_id: int) -> None:
        res = await self.bot.db.execute("DELETE FROM cards WHERE id=? AND user_id=?", (card_id, interaction.user.id))
        await interaction.response.send_message("🗑️ Deleted." if res.rowcount else "No card with that id.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Flashcards(bot))
