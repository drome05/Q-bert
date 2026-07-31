import asyncio
import logging

import discord
from aiohttp import web
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
    "cogs.music",
)


async def _healthz_live(request):
    return web.json_response({"status": "alive"})


class DiscordBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        super().__init__(command_prefix="!", intents=intents)
        self._health_runner = None

    async def _healthz_ready(self, request):
        if self.is_ready():
            return web.json_response({"status": "ready"})
        return web.json_response({"status": "not_ready"}, status=503)

    async def _start_health_server(self):
        app = web.Application()
        app.router.add_get("/healthz/live", _healthz_live)
        app.router.add_get("/healthz/ready", self._healthz_ready)
        self._health_runner = web.AppRunner(app)
        await self._health_runner.setup()
        site = web.TCPSite(self._health_runner, "0.0.0.0", config.HEALTH_PORT)
        await site.start()
        logger.info("Health server listening on :%d", config.HEALTH_PORT)

    async def setup_hook(self):
        await start_all()
        await self._start_health_server()
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
        if self._health_runner is not None:
            await self._health_runner.cleanup()
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
