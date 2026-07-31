"""Blackjack, Coinflip, and Slots -- each its own top-level command AND its
own backend service/pod (blackjack-service, coinflip-service, slots-service,
all in the casino namespace). This cog only renders Discord embeds from
whatever JSON the relevant service returns and forwards button clicks to it.
"""
import logging

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils import embeds, settings_client as settings
from utils.clients import blackjack_client, coinflip_client, slots_client, db_client
from utils.service_client import ServiceError

logger = logging.getLogger("bot.casino")


def card_str(card) -> str:
    return f"{card[0]}{card[1]}"


def hand_str(cards) -> str:
    return " ".join(card_str(c) for c in cards)


def _render_blackjack(state: dict, currency_name: str) -> discord.Embed:
    embed = discord.Embed(title="🃏 Blackjack", color=discord.Color.dark_green())
    embed.add_field(name="Your hand", value=f"{hand_str(state['player_hand'])} ({state['player_total']})", inline=False)
    if state["finished"]:
        embed.add_field(name="Dealer's hand", value=f"{hand_str(state['dealer_hand'])} ({state['dealer_total']})", inline=False)
    else:
        upcard = state["dealer_hand"][0]
        embed.add_field(name="Dealer's hand", value=f"{card_str(upcard)} ??", inline=False)
    embed.set_footer(text=f"Bet: {state['bet']:,} {currency_name}")
    if state["finished"]:
        embed.add_field(name="Result", value=f"{state['result_text']}\nBalance: **{state['new_balance']:,}**", inline=False)
    return embed


class BlackjackView(discord.ui.View):
    def __init__(self, guild_id: str, user_id: str, state: dict, currency_name: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.state = state
        self.currency_name = currency_name
        if not state["can_double"]:
            self.double_down.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    async def _apply(self, interaction: discord.Interaction, path: str):
        try:
            state = await blackjack_client.post(path, {"guild_id": self.guild_id, "user_id": self.user_id})
        except ServiceError as e:
            await interaction.response.send_message(embed=embeds.error_embed(e.body.get("error", "Something went wrong.")), ephemeral=True)
            return

        if state["finished"]:
            for child in self.children:
                child.disabled = True
            result_view = BlackjackResultView(self.guild_id, self.user_id, state["bet"], self.currency_name)
            await interaction.response.edit_message(embed=_render_blackjack(state, self.currency_name), view=result_view)
            return

        self.state = state
        if not state["can_double"]:
            self.double_down.disabled = True
        await interaction.response.edit_message(embed=_render_blackjack(state, self.currency_name), view=self)

    @discord.ui.button(label="Hit", style=discord.ButtonStyle.primary)
    async def hit(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._apply(interaction, "/hit")

    @discord.ui.button(label="Stand", style=discord.ButtonStyle.secondary)
    async def stand(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._apply(interaction, "/stand")

    @discord.ui.button(label="Double Down", style=discord.ButtonStyle.danger)
    async def double_down(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._apply(interaction, "/double")


class BlackjackResultView(discord.ui.View):
    """Lets the player deal another hand at the same bet without retyping /blackjack."""

    def __init__(self, guild_id: str, user_id: str, bet: int, currency_name: str):
        super().__init__(timeout=120)
        self.guild_id = guild_id
        self.user_id = user_id
        self.bet = bet
        self.currency_name = currency_name

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) != self.user_id:
            await interaction.response.send_message("This isn't your game.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Play Again", style=discord.ButtonStyle.success)
    async def play_again(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        try:
            state = await blackjack_client.post("/start", {"guild_id": self.guild_id, "user_id": self.user_id, "bet": self.bet})
        except ServiceError as e:
            error_map = {"game_in_progress": "You already have a blackjack game in progress.", "insufficient_balance": "You don't have enough coins for that bet."}
            await interaction.response.send_message(embed=embeds.error_embed(error_map.get(e.body.get("error"), "Something went wrong.")), ephemeral=True)
            return

        if state["finished"]:
            result_view = BlackjackResultView(self.guild_id, self.user_id, self.bet, self.currency_name)
            await interaction.response.edit_message(embed=_render_blackjack(state, self.currency_name), view=result_view)
        else:
            await interaction.response.edit_message(embed=_render_blackjack(state, self.currency_name), view=BlackjackView(self.guild_id, self.user_id, state, self.currency_name))


class CoinflipChallengeView(discord.ui.View):
    def __init__(self, guild_id: str, challenger: discord.Member, opponent: discord.Member, amount: int, currency_name: str):
        super().__init__(timeout=config.COINFLIP_TIMEOUT_SECONDS)
        self.guild_id = guild_id
        self.challenger = challenger
        self.opponent = opponent
        self.amount = amount
        self.currency_name = currency_name
        self.resolved = False

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.opponent.id:
            await interaction.response.send_message("This challenge isn't for you.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True

        try:
            result = await coinflip_client.post(
                "/resolve",
                {"guild_id": self.guild_id, "challenger_id": str(self.challenger.id), "opponent_id": str(self.opponent.id), "amount": self.amount},
            )
        except ServiceError as e:
            who = self.challenger if e.body.get("error") == "challenger_insufficient_balance" else self.opponent
            embed = embeds.error_embed(f"{who.mention} no longer has enough coins. Challenge cancelled.")
            await interaction.response.edit_message(embed=embed, view=self)
            self.stop()
            return

        winner = self.challenger if str(self.challenger.id) == result["winner_id"] else self.opponent
        loser = self.opponent if winner is self.challenger else self.challenger
        embed = discord.Embed(
            title="🪙 Coinflip Result",
            description=f"{winner.mention} wins **{result['payout']:,}** {self.currency_name} from {loser.mention}!",
            color=discord.Color.gold(),
        )
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if self.resolved:
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True
        embed = discord.Embed(description=f"{self.opponent.mention} declined the challenge.", color=discord.Color.red())
        await interaction.response.edit_message(embed=embed, view=self)
        self.stop()

    async def on_timeout(self):
        if self.resolved:
            return
        self.resolved = True
        for child in self.children:
            child.disabled = True


class Casino(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="blackjack", description="Play a hand of blackjack")
    @app_commands.describe(bet="How many coins to bet")
    @app_commands.guild_only()
    async def blackjack(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        guild_id = str(interaction.guild_id)
        guild_settings = await settings.get(interaction.guild_id)
        currency_name = guild_settings["currency_name"]
        try:
            state = await blackjack_client.post("/start", {"guild_id": guild_id, "user_id": str(interaction.user.id), "bet": bet})
        except ServiceError as e:
            error_map = {"game_in_progress": "You already have a blackjack game in progress.", "insufficient_balance": "You don't have enough coins for that bet."}
            await interaction.response.send_message(embed=embeds.error_embed(error_map.get(e.body.get("error"), "Something went wrong.")), ephemeral=True)
            return

        if state["finished"]:
            await interaction.response.send_message(
                embed=_render_blackjack(state, currency_name),
                view=BlackjackResultView(guild_id, str(interaction.user.id), bet, currency_name),
            )
            return
        await interaction.response.send_message(
            embed=_render_blackjack(state, currency_name), view=BlackjackView(guild_id, str(interaction.user.id), state, currency_name)
        )

    @app_commands.command(name="coinflip", description="Challenge another member to a 1v1 coin wager")
    @app_commands.describe(opponent="Who to challenge", amount="How many coins to wager")
    @app_commands.guild_only()
    async def coinflip(self, interaction: discord.Interaction, opponent: discord.Member, amount: app_commands.Range[int, 1]):
        if opponent.id == interaction.user.id:
            await interaction.response.send_message(embed=embeds.error_embed("You can't challenge yourself."), ephemeral=True)
            return
        if opponent.bot:
            await interaction.response.send_message(embed=embeds.error_embed("You can't challenge a bot."), ephemeral=True)
            return

        guild_id = str(interaction.guild_id)
        challenger_id = str(interaction.user.id)
        await db_client.post("/users/ensure", {"user_id": challenger_id})
        economy_row = await db_client.get(f"/economy/{guild_id}/{challenger_id}")
        if economy_row["balance"] < amount:
            await interaction.response.send_message(embed=embeds.error_embed("You don't have enough coins for that wager."), ephemeral=True)
            return

        guild_settings = await settings.get(interaction.guild_id)
        embed = discord.Embed(
            title="🪙 Coinflip Challenge",
            description=f"{interaction.user.mention} challenges {opponent.mention} to a **{amount:,} {guild_settings['currency_name']}** coinflip!",
            color=discord.Color.blue(),
        )
        view = CoinflipChallengeView(guild_id, interaction.user, opponent, amount, guild_settings["currency_name"])
        await interaction.response.send_message(content=opponent.mention, embed=embed, view=view)

    @app_commands.command(name="slots", description="Spin the slot machine")
    @app_commands.describe(bet="How many coins to bet")
    @app_commands.guild_only()
    async def slots(self, interaction: discord.Interaction, bet: app_commands.Range[int, 1]):
        guild_settings = await settings.get(interaction.guild_id)
        try:
            result = await slots_client.post("/spin", {"guild_id": str(interaction.guild_id), "user_id": str(interaction.user.id), "bet": bet})
        except ServiceError:
            await interaction.response.send_message(embed=embeds.error_embed("You don't have enough coins for that bet."), ephemeral=True)
            return

        embed = discord.Embed(title="🎰 Slots", description=f"[ {'  |  '.join(result['reels'])} ]", color=discord.Color.purple())
        if result["payout"] > 0:
            embed.add_field(name="Result", value=f"You win **{result['payout']:,}** {guild_settings['currency_name']}! Balance: **{result['new_balance']:,}**")
        else:
            embed.add_field(name="Result", value=f"No match. You lose your **{bet:,}** bet. Balance: **{result['new_balance']:,}**")
        await interaction.response.send_message(embed=embed)


async def setup(bot: commands.Bot):
    await bot.add_cog(Casino(bot))
