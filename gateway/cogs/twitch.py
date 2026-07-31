"""Twitch live announcements: self-service linking + staff moderation
commands. All Helix API calls and live-check logic now live in
twitch-service; this cog renders embeds and drives the gateway-side
scheduled poll (only the gateway can call channel.send()).
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import embeds, settings_client as settings
from utils.clients import twitch_client
from utils.permissions import require_staff
from utils.service_client import ServiceError

logger = logging.getLogger("bot.twitch")

TWITCH_POLL_INTERVAL_MINUTES = 3


def _announcement_embed(stream: dict) -> discord.Embed:
    embed = discord.Embed(
        title=stream.get("title") or "Live on Twitch!",
        url=f"https://twitch.tv/{stream.get('user_login', '')}",
        description=f"Playing **{stream.get('game_name') or 'something'}**",
        color=discord.Color.purple(),
    )
    if stream.get("thumbnail_url"):
        embed.set_image(url=stream["thumbnail_url"])
    embed.set_footer(text=f"{stream.get('viewer_count', 0):,} viewers")
    return embed


def _not_configured_embed() -> discord.Embed:
    return embeds.error_embed(
        "Twitch integration isn't configured yet -- ask the bot owner to set "
        "TWITCH_CLIENT_ID and TWITCH_CLIENT_SECRET (from https://dev.twitch.tv/console/apps)."
    )


class Twitch(commands.Cog):
    twitch_group = app_commands.Group(name="twitch", description="Twitch live announcement commands", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.live_poll.start()

    async def cog_unload(self):
        self.live_poll.cancel()

    @twitch_group.command(name="link", description="Link your Twitch channel for live announcements")
    @app_commands.describe(username="Your Twitch username (from twitch.tv/<username>)")
    async def link(self, interaction: discord.Interaction, username: str):
        await twitch_client.post("/link", {"guild_id": str(interaction.guild_id), "user_id": str(interaction.user.id), "twitch_username": username})
        await interaction.response.send_message(f"Linked to Twitch channel **{username}**.", ephemeral=True)

    @twitch_group.command(name="unlink", description="Unlink your Twitch channel")
    async def unlink(self, interaction: discord.Interaction):
        try:
            await twitch_client.delete(f"/unlink/{interaction.guild_id}/{interaction.user.id}")
        except ServiceError:
            await interaction.response.send_message(embed=embeds.error_embed("You don't have a Twitch channel linked."), ephemeral=True)
            return
        await interaction.response.send_message("Unlinked your Twitch channel.", ephemeral=True)

    @twitch_group.command(name="list", description="Staff: list every linked Twitch channel")
    @require_staff()
    async def list_accounts(self, interaction: discord.Interaction):
        rows = await twitch_client.get(f"/accounts/{interaction.guild_id}")
        if not rows:
            await interaction.response.send_message("No one has linked a Twitch channel yet.")
            return
        lines = []
        for row in rows:
            status = "🔴 live" if row["is_live"] else "⚫ offline"
            lines.append(f"<@{row['user_id']}> — [{row['twitch_username']}](https://twitch.tv/{row['twitch_username']}) ({status})")
        embed = discord.Embed(title="Linked Twitch Channels", description="\n".join(lines), color=discord.Color.purple())
        embed.set_footer(text=f"Live status refreshes every ~{TWITCH_POLL_INTERVAL_MINUTES} min")
        await interaction.response.send_message(embed=embed)

    @twitch_group.command(name="unlink-user", description="Staff: force-unlink another member's Twitch channel")
    @app_commands.describe(member="Whose link to remove")
    @require_staff()
    async def unlink_user(self, interaction: discord.Interaction, member: discord.Member):
        try:
            await twitch_client.delete(f"/unlink/{interaction.guild_id}/{member.id}")
        except ServiceError:
            await interaction.response.send_message(embed=embeds.error_embed(f"{member.display_name} doesn't have a Twitch channel linked."), ephemeral=True)
            return
        await interaction.response.send_message(f"Unlinked {member.mention}'s Twitch channel.")

    @twitch_group.command(name="announce", description="Staff: check a member's live status now and post the announcement if they're live")
    @app_commands.describe(member="Whose live status to check")
    @require_staff()
    async def announce(self, interaction: discord.Interaction, member: discord.Member):
        await interaction.response.defer()
        result = await twitch_client.get(f"/live-status/{interaction.guild_id}/{member.id}")
        if not result["linked"]:
            await interaction.followup.send(embed=embeds.error_embed(f"{member.display_name} hasn't linked a Twitch channel."))
            return
        if result.get("error") == "unavailable":
            await interaction.followup.send("Couldn't reach the Twitch API right now. Try again later.")
            return
        if result.get("error") == "not_configured":
            await interaction.followup.send(embed=_not_configured_embed())
            return
        if not result["live"]:
            await interaction.followup.send(f"{member.display_name} isn't currently live on Twitch.")
            return

        guild_settings = await settings.get(interaction.guild_id)
        channel_id = guild_settings["twitch_announcements_channel_id"]
        channel = self.bot.get_channel(int(channel_id)) if channel_id else interaction.channel

        await channel.send(content=f"🔴 <@{member.id}> is now live on Twitch!", embed=_announcement_embed(result["stream"]))
        await interaction.followup.send(f"Posted the announcement in {channel.mention}.")

    @tasks.loop(minutes=TWITCH_POLL_INTERVAL_MINUTES)
    async def live_poll(self):
        # Runs once per guild the bot is actually in -- not a single global
        # scan -- since linked accounts are now isolated per-server.
        for guild in self.bot.guilds:
            guild_settings = await settings.get(guild.id)
            channel_id = guild_settings["twitch_announcements_channel_id"]
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            if not channel:
                continue

            try:
                result = await twitch_client.post("/live-check", {"guild_id": str(guild.id)})
            except ServiceError as e:
                if e.body.get("error") != "not_configured":
                    logger.warning("Twitch live-poll call failed for guild %s: %s", guild.id, e)
                continue
            for event in result["events"]:
                await channel.send(content=f"🔴 <@{event['user_id']}> is now live on Twitch!", embed=_announcement_embed(event["stream"]))

    @live_poll.before_loop
    async def before_live_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Twitch(bot))
