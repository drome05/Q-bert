"""Valorant tracker: link, rank, matches, and an hourly rank-up poller.
All HenrikDev calls and rank-up detection logic now live in
valorant-service; this cog just renders Discord embeds and drives the
gateway-side scheduled poll (only the gateway can call channel.send()).

Deliberately does NOT implement /valorant store or /valorant link-store --
see valorant-service for the reasoning (credential-harvesting risk).
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils import settings_client as settings
from utils.clients import valorant_client
from utils.service_client import ServiceError
from utils.views import PaginatedEmbedView

logger = logging.getLogger("bot.valorant")

MATCH_MODE_CHOICES = [
    app_commands.Choice(name="Competitive", value="competitive"),
    app_commands.Choice(name="Unrated", value="unrated"),
    app_commands.Choice(name="Swiftplay", value="swiftplay"),
    app_commands.Choice(name="Spike Rush", value="spikerush"),
    app_commands.Choice(name="Deathmatch", value="deathmatch"),
    app_commands.Choice(name="Custom", value="custom"),
]
VALORANT_REGIONS = ["na", "eu", "ap", "kr", "latam", "br"]
VALORANT_MATCHES_MAX_COUNT = 10
VALORANT_MATCHES_DEFAULT_COUNT = 5


class Valorant(commands.Cog):
    valorant_group = app_commands.Group(name="valorant", description="Valorant tracker commands", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        self.rankup_poll.start()

    async def cog_unload(self):
        self.rankup_poll.cancel()

    @valorant_group.command(name="link", description="Link your Riot ID to your Discord account")
    @app_commands.describe(riot_name="Your Riot name (without the #tag)", riot_tag="Your Riot tag (without the #)", region="Your region")
    @app_commands.choices(region=[app_commands.Choice(name=r, value=r) for r in VALORANT_REGIONS])
    async def link(self, interaction: discord.Interaction, riot_name: str, riot_tag: str, region: str = "na"):
        try:
            await valorant_client.post("/link", {"guild_id": str(interaction.guild_id), "user_id": str(interaction.user.id), "riot_name": riot_name, "riot_tag": riot_tag, "region": region})
        except ServiceError as e:
            valid = e.body.get("valid_regions", VALORANT_REGIONS)
            await interaction.response.send_message(f"Region must be one of: {', '.join(valid)}", ephemeral=True)
            return
        await interaction.response.send_message(f"Linked to **{riot_name}#{riot_tag}** ({region.upper()}).", ephemeral=True)

    @valorant_group.command(name="rank", description="Show a linked account's current Valorant rank")
    @app_commands.describe(user="Whose rank to check (defaults to you)")
    async def rank(self, interaction: discord.Interaction, user: discord.Member | None = None):
        target = user or interaction.user
        await interaction.response.defer()
        result = await valorant_client.get(f"/rank/{interaction.guild_id}/{target.id}")
        if not result["linked"]:
            who = "You haven't" if target.id == interaction.user.id else f"{target.display_name} hasn't"
            await interaction.followup.send(f"{who} linked a Riot account yet. Use `/valorant link` first.")
            return
        if not result["found"]:
            if result.get("error") == "account_not_found":
                await interaction.followup.send(
                    f"Riot couldn't find **{target.display_name}**'s linked Riot ID -- double check the exact name/tag "
                    f"(shown in-game as `Name#TAG`) and re-link with `/valorant link`."
                )
            else:
                await interaction.followup.send("Couldn't reach the Valorant rank service right now. Try again later.")
            return

        embed = discord.Embed(title=f"{target.display_name}'s Valorant Rank", color=discord.Color.red())
        embed.add_field(name="Current Rank", value=f"{result['tier']} ({result['rr']} RR)", inline=False)
        if result.get("peak"):
            embed.add_field(name="Peak (this act)", value=result["peak"], inline=False)
        if result.get("icon"):
            embed.set_thumbnail(url=result["icon"])

        if result.get("games_played"):
            embed.add_field(name="K/D", value=str(result["kd"]), inline=True)
            embed.add_field(name="Avg ACS", value=str(result["avg_acs"]), inline=True)
            embed.add_field(name="Avg ADR", value=str(result["avg_adr"]), inline=True)
            embed.add_field(name="Headshot %", value=f"{result['headshot_pct']}%", inline=True)
            embed.add_field(name="Overall Grade", value=result["grade"], inline=True)
            embed.set_footer(text=f"Stats from last {result['games_played']} games -- grade is an unofficial estimate, not a Riot stat")
        else:
            embed.set_footer(text="No recent match history to compute performance stats yet")

        await interaction.followup.send(embed=embed)

    @valorant_group.command(name="matches", description="Show a linked account's recent match history")
    @app_commands.describe(user="Whose matches to check (defaults to you)", count="How many recent matches (max 10)", mode="Filter by game mode (defaults to all modes)")
    @app_commands.choices(mode=MATCH_MODE_CHOICES)
    async def matches(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        count: app_commands.Range[int, 1, VALORANT_MATCHES_MAX_COUNT] = VALORANT_MATCHES_DEFAULT_COUNT,
        mode: app_commands.Choice[str] | None = None,
    ):
        target = user or interaction.user
        await interaction.response.defer()
        result = await valorant_client.get(f"/matches/{interaction.guild_id}/{target.id}", params={"count": count, **({"mode": mode.value} if mode else {})})
        if not result["linked"]:
            who = "You haven't" if target.id == interaction.user.id else f"{target.display_name} hasn't"
            await interaction.followup.send(f"{who} linked a Riot account yet. Use `/valorant link` first.")
            return
        if not result["found"]:
            if result.get("error") == "account_not_found":
                await interaction.followup.send(
                    f"Riot couldn't find **{target.display_name}**'s linked Riot ID -- double check the exact name/tag "
                    f"(shown in-game as `Name#TAG`) and re-link with `/valorant link`."
                )
            else:
                await interaction.followup.send("Couldn't reach the Valorant match history service right now. Try again later.")
            return

        match_list = result["matches"]
        if not match_list:
            await interaction.followup.send("No recent matches found.")
            return

        # Discord embeds only support one thumbnail/image each, so each match
        # gets its own embed (with the agent's icon) and we page through them.
        pages = []
        for m in match_list:
            result_text = "Win" if m["won"] else "Loss"
            embed = discord.Embed(
                title=f"{m['map']} — {result_text} ({m['team_score']}-{m['other_score']})",
                description=f"{m['mode']} • {m['character']}",
                color=discord.Color.green() if m["won"] else discord.Color.red(),
            )
            embed.add_field(name="K / D / A", value=f"{m['kills']} / {m['deaths']} / {m['assists']}")
            embed.add_field(name="Score", value=str(m["score"]))
            # ACS/ADR are None for non-round modes (e.g. Deathmatch), where they aren't meaningful
            embed.add_field(name="ACS", value=str(round(m["acs"], 1)) if m["acs"] is not None else "N/A")
            embed.add_field(name="ADR", value=str(round(m["adr"], 1)) if m["adr"] is not None else "N/A")
            embed.add_field(name="Headshot %", value=f"{round(m['headshot_pct'], 1)}%")
            if m.get("match_grade"):
                embed.add_field(name="Grade", value=m["match_grade"])
            if m.get("tips"):
                embed.add_field(name="Tips", value="\n".join(m["tips"]), inline=False)
            if m.get("agent_icon"):
                embed.set_thumbnail(url=m["agent_icon"])
            embed.set_footer(text=f"{target.display_name}'s Recent Matches — {len(pages) + 1}/{len(match_list)} — Grade/tips are unofficial estimates")
            pages.append(embed)

        if len(pages) == 1:
            await interaction.followup.send(embed=pages[0])
        else:
            await interaction.followup.send(embed=pages[0], view=PaginatedEmbedView(pages))

    @tasks.loop(hours=1)
    async def rankup_poll(self):
        # Runs once per guild the bot is actually in -- not a single global
        # scan -- since accounts/rewards are now isolated per-server.
        for guild in self.bot.guilds:
            guild_settings = await settings.get(guild.id)
            channel_id = guild_settings["valorant_updates_channel_id"]
            channel = self.bot.get_channel(int(channel_id)) if channel_id else None
            if not channel:
                continue

            try:
                result = await valorant_client.post("/rankup-check", {"guild_id": str(guild.id)})
            except ServiceError as e:
                logger.warning("Rank-up poll call failed for guild %s: %s", guild.id, e)
                continue
            for event in result["events"]:
                await channel.send(
                    f"🎉 <@{event['user_id']}> ranked up to **{event['new_tier']}**! (+{event['reward']:,} {guild_settings['currency_name']})"
                )

    @rankup_poll.before_loop
    async def before_rankup_poll(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Valorant(bot))
