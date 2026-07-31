"""Fun easter egg: replies "hey" when someone says "qbert say hi" (or
say hello/hey) in a plain message, rather than a slash command. Needs the
privileged Message Content intent (enabled in bot.py + the Discord
Developer Portal) since this reads raw message text.
"""
import random
import re

import discord
from discord.ext import commands

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


async def setup(bot: commands.Bot):
    await bot.add_cog(Greetings(bot))
