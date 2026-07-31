import discord
from discord import app_commands

from utils import settings_client as settings


async def is_staff(interaction: discord.Interaction) -> bool:
    if interaction.user.guild_permissions.administrator:
        return True
    row = await settings.get(interaction.guild_id)
    staff_role_id = row["inhouse_staff_role_id"]
    if staff_role_id is None:
        return False
    return any(str(r.id) == staff_role_id for r in getattr(interaction.user, "roles", []))


def require_staff():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not await is_staff(interaction):
            raise app_commands.CheckFailure("You need to be staff/admin to use this command.")
        return True
    return app_commands.check(predicate)
