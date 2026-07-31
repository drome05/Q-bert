import logging
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils import embeds, settings_client as settings
from utils.clients import economy_client
from utils.service_client import ServiceError

logger = logging.getLogger("bot.economy")


class Economy(commands.Cog):
    economy_group = app_commands.Group(name="economy", description="Coin economy commands")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @economy_group.command(name="balance", description="Show your (or someone's) currency balance")
    @app_commands.describe(user="Whose balance to check (defaults to you)")
    async def balance(self, interaction: discord.Interaction, user: discord.Member | None = None):
        target = user or interaction.user
        result = await economy_client.get(f"/balance/{target.id}")
        guild_settings = await settings.get(interaction.guild_id)
        await interaction.response.send_message(
            embed=embeds.balance_embed(target, result["balance"], guild_settings["currency_name"], guild_settings["currency_emoji"])
        )

    async def _claim(self, interaction: discord.Interaction, period: str):
        result = await economy_client.post("/claim", {"user_id": str(interaction.user.id), "period": period})
        if not result["claimed"]:
            ready_at = int(time.time() + result["remaining_seconds"])
            await interaction.response.send_message(
                embed=embeds.error_embed(f"You already claimed your {period}. Try again <t:{ready_at}:R>."),
                ephemeral=True,
            )
            return

        guild_settings = await settings.get(interaction.guild_id)
        embed = discord.Embed(
            title=f"{guild_settings['currency_emoji']} {period.capitalize()} claimed!",
            description=f"You received **{result['amount']:,} {guild_settings['currency_name']}**. New balance: **{result['new_balance']:,}**.",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    @economy_group.command(name="daily", description="Claim your daily coin bonus")
    async def daily(self, interaction: discord.Interaction):
        await self._claim(interaction, "daily")

    @economy_group.command(name="weekly", description="Claim your weekly coin bonus")
    async def weekly(self, interaction: discord.Interaction):
        await self._claim(interaction, "weekly")

    @economy_group.command(name="monthly", description="Claim your monthly coin bonus")
    async def monthly(self, interaction: discord.Interaction):
        await self._claim(interaction, "monthly")

    @economy_group.command(name="leaderboard", description="Top 10 currency balances")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await economy_client.get("/leaderboard", params={"limit": 10})
        pairs = [(r["user_id"], r["balance"]) for r in rows]
        guild_settings = await settings.get(interaction.guild_id)
        await interaction.response.send_message(
            embed=embeds.economy_leaderboard_embed(pairs, interaction.guild, guild_settings["currency_name"], guild_settings["currency_emoji"])
        )

    @economy_group.command(name="give", description="Give some of your currency to another member")
    @app_commands.describe(user="Who to give coins to", amount="How many coins to give")
    async def give(self, interaction: discord.Interaction, user: discord.Member, amount: app_commands.Range[int, 1]):
        if user.id == interaction.user.id:
            await interaction.response.send_message(embed=embeds.error_embed("You can't give coins to yourself."), ephemeral=True)
            return
        if user.bot:
            await interaction.response.send_message(embed=embeds.error_embed("You can't give coins to a bot."), ephemeral=True)
            return

        try:
            await economy_client.post("/give", {"giver_id": str(interaction.user.id), "receiver_id": str(user.id), "amount": amount})
        except ServiceError:
            await interaction.response.send_message(embed=embeds.error_embed("You don't have enough coins for that."), ephemeral=True)
            return

        guild_settings = await settings.get(interaction.guild_id)
        embed = discord.Embed(
            description=f"{interaction.user.mention} gave **{amount:,} {guild_settings['currency_name']}** to {user.mention}!",
            color=discord.Color.gold(),
        )
        await interaction.response.send_message(embed=embed)

    @economy_group.command(name="admin-adjust", description="Admin: add or remove coins from a user's balance")
    @app_commands.describe(user="Who to adjust", amount="Amount to add (negative to remove)", reason="Why you're making this adjustment")
    @app_commands.checks.has_permissions(administrator=True)
    async def admin_adjust(self, interaction: discord.Interaction, user: discord.Member, amount: int, reason: str):
        result = await economy_client.post("/admin-adjust", {"user_id": str(user.id), "amount": amount, "reason": reason})
        logger.info("Admin %s adjusted %s by %d (%s). New balance: %d", interaction.user.id, user.id, amount, reason, result["new_balance"])
        guild_settings = await settings.get(interaction.guild_id)
        embed = discord.Embed(
            description=f"Adjusted {user.mention} by **{amount:+,} {guild_settings['currency_name']}** ({reason}). New balance: **{result['new_balance']:,}**.",
            color=discord.Color.orange(),
        )
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Economy(bot))
