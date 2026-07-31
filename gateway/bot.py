import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.clients import start_all, close_all

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

INITIAL_COGS = (
    "cogs.economy",
    "cogs.casino",
    "cogs.valorant",
    "cogs.inhouse",
    "cogs.settings",
    "cogs.twitch",
)


class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await start_all()
        for cog in INITIAL_COGS:
            await self.load_extension(cog)
            logger.info("Loaded extension %s", cog)

        guild = discord.Object(id=config.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        logger.info("Synced %d commands to guild %s", len(synced), config.GUILD_ID)

    async def on_ready(self):
        logger.info("Logged in as %s (id: %s)", self.user, self.user.id)

    async def close(self):
        await close_all()
        await super().close()


bot = DiscordBot()


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.warning("App command error in /%s: %s", interaction.command.qualified_name if interaction.command else "?", error, exc_info=error)
    message = "Something went wrong running that command."
    if isinstance(error, app_commands.MissingPermissions):
        message = "You don't have permission to do that."
    elif isinstance(error, app_commands.CommandOnCooldown):
        message = str(error)
    elif isinstance(error, app_commands.CheckFailure):
        message = str(error) or "You can't use that command."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Failed to send error response to interaction")


async def main():
    async with bot:
        await bot.start(config.DISCORD_BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
