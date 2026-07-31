"""The in-Discord settings "dashboard" -- replaces editing .env by hand for
the per-server tunables that used to live only in env vars. Secrets
(DISCORD_BOT_TOKEN, HENRIKDEV_API_KEY) and boot-time config (GUILD_ID,
DB_PATH) stay env-only; those aren't safe or sensible to expose as a
Discord-editable command.
"""
import discord
from discord import app_commands
from discord.ext import commands

from utils import settings_client as settings
from utils.permissions import require_staff


class Settings(commands.Cog):
    settings_group = app_commands.Group(name="settings", description="Configure this server's bot settings")

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @settings_group.command(name="show", description="Show this server's current bot settings")
    @require_staff()
    async def show(self, interaction: discord.Interaction):
        row = await settings.get(interaction.guild_id)
        embed = discord.Embed(title="Bot Settings", color=discord.Color.blurple())
        embed.add_field(name="Currency", value=f"{row['currency_emoji']} {row['currency_name']}", inline=False)
        embed.add_field(
            name="Valorant Updates Channel",
            value=f"<#{row['valorant_updates_channel_id']}>" if row["valorant_updates_channel_id"] else "Not set",
            inline=False,
        )
        embed.add_field(
            name="Staff Role",
            value=f"<@&{row['inhouse_staff_role_id']}>" if row["inhouse_staff_role_id"] else "Not set (admins only)",
            inline=False,
        )
        embed.add_field(
            name="Inhouse Voice Category",
            value=f"<#{row['inhouse_voice_category_id']}>" if row["inhouse_voice_category_id"] else "Not set (created at top level)",
            inline=False,
        )
        embed.add_field(
            name="Twitch Announcements Channel",
            value=f"<#{row['twitch_announcements_channel_id']}>" if row["twitch_announcements_channel_id"] else "Not set",
            inline=False,
        )
        await interaction.response.send_message(embed=embed)

    @settings_group.command(name="currency", description="Rename the server's coin currency")
    @app_commands.describe(name="The new currency name (e.g. 'Pink Slips')", emoji="An emoji to represent it (e.g. \U0001f3c1)")
    @require_staff()
    async def currency(self, interaction: discord.Interaction, name: str, emoji: str):
        await settings.set_currency(interaction.guild_id, name, emoji)
        await interaction.response.send_message(f"Currency renamed to {emoji} **{name}**.")

    @settings_group.command(name="valorant-channel", description="Set the channel for Valorant rank-up announcements")
    @app_commands.describe(channel="The channel to post rank-up announcements in")
    @require_staff()
    async def valorant_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await settings.set_valorant_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"Valorant rank-up announcements will now post in {channel.mention}.")

    @settings_group.command(name="staff-role", description="Set the staff/mod role for inhouse disputes/overrides and Twitch moderation commands")
    @app_commands.describe(role="The staff/mod role")
    @require_staff()
    async def staff_role(self, interaction: discord.Interaction, role: discord.Role):
        await settings.set_staff_role(interaction.guild_id, role.id)
        await interaction.response.send_message(f"Staff role set to {role.mention}.")

    @settings_group.command(name="voice-category", description="Set the category temporary inhouse match channels are created under")
    @app_commands.describe(category="The category for inhouse match voice/text channels")
    @require_staff()
    async def voice_category(self, interaction: discord.Interaction, category: discord.CategoryChannel):
        await settings.set_voice_category(interaction.guild_id, category.id)
        await interaction.response.send_message(f"Inhouse match channels will now be created under **{category.name}**.")

    @settings_group.command(name="twitch-channel", description="Set the channel for Twitch live announcements")
    @app_commands.describe(channel="The channel to post live announcements in")
    @require_staff()
    async def twitch_channel(self, interaction: discord.Interaction, channel: discord.TextChannel):
        await settings.set_twitch_channel(interaction.guild_id, channel.id)
        await interaction.response.send_message(f"Twitch live announcements will now post in {channel.mention}.")


async def setup(bot: commands.Bot):
    await bot.add_cog(Settings(bot))
