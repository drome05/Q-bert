"""Fun easter egg: replies when someone addresses the bot by name in a
plain message -- e.g. "qbert say hi" -- rather than a slash command.
Needs the privileged Message Content intent (enabled in bot.py + the
Discord Developer Portal) since this reads raw message text.
"""
import logging
import random
import re

import discord
from discord.ext import commands

logger = logging.getLogger("bot.greetings")

# "qbert"/"q-bert"/"q bert" (case-insensitive), followed later in the
# message by "say hi"/"say hello"/"say hey".
TRIGGER_RE = re.compile(r"\bq[\s-]?bert\b.*\bsay\s+(hi|hello|hey)\b", re.IGNORECASE)

GREETINGS = [
    "Hey! 👋",
    "Yo, what's good?",
    "Sup! 🏁",
    "Hey hey hey!",
    "o/",
    "'Sup, champ.",
]


class Greetings(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or message.guild is None:
            return
        if TRIGGER_RE.search(message.content):
            await message.reply(random.choice(GREETINGS), mention_author=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(Greetings(bot))
