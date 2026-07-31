"""Fun easter egg: replies when someone addresses the bot by name in a
plain message -- e.g. "qbert say hi" or "qbert what's your favorite map" --
rather than a slash command. Needs the privileged Message Content intent
(enabled in bot.py + the Discord Developer Portal) since this reads raw
message text.

"say hi"/"say hello"/"say hey" stays a free, instant canned response --
no reason to spend an LLM call on it. Anything else that mentions the
bot's name gets routed to chat-service (a local Ollama model) for a real
generated reply.
"""
import logging
import random
import re

import discord
from discord.ext import commands

from utils.clients import chat_client
from utils.service_client import ServiceError

logger = logging.getLogger("bot.greetings")

NAME_RE = re.compile(r"\bq[\s-]?bert\b", re.IGNORECASE)
SAY_HI_RE = re.compile(r"\bsay\s+(hi|hello|hey)\b", re.IGNORECASE)

GREETINGS = [
    "Hey! 👋",
    "Yo, what's good?",
    "Sup! 🏁",
    "Hey hey hey!",
    "o/",
    "'Sup, champ.",
]

CHAT_UNAVAILABLE_REPLIES = [
    "brain's not loaded right now, try again in a sec",
    "my brain's taking a nap, ask me again shortly",
]


class Greetings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if not NAME_RE.search(message.content):
            return

        if SAY_HI_RE.search(message.content):
            await message.reply(random.choice(GREETINGS), mention_author=False)
            return

        async with message.channel.typing():
            try:
                result = await chat_client.post("/chat", {"message": message.content})
            except ServiceError as e:
                logger.warning("chat-service call failed: %s", e)
                await message.reply(random.choice(CHAT_UNAVAILABLE_REPLIES), mention_author=False)
                return
        await message.reply(result["reply"], mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Greetings(bot))
