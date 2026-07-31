"""Dynamic /help command -- walks the bot's actual registered command
tree rather than a hardcoded list, so it can't go stale as commands are
added, renamed, or removed across the other cogs.
"""
import discord
from discord import app_commands
from discord.ext import commands


def _format_command(cmd: app_commands.Command) -> str:
    lock = "🔒 " if cmd.checks else ""
    return f"{lock}`/{cmd.qualified_name}` — {cmd.description}"


class Help(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show all available commands")
    async def help(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="📖 Command Help",
            description="🔒 = staff/admin only",
            color=discord.Color.blurple(),
        )
        for cog_name, cog in sorted(self.bot.cogs.items()):
            lines = []
            for command in cog.get_app_commands():
                if isinstance(command, app_commands.Group):
                    lines.extend(_format_command(sub) for sub in command.commands)
                else:
                    lines.append(_format_command(command))
            if lines:
                embed.add_field(name=cog_name, value="\n".join(sorted(lines)), inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(Help(bot))
