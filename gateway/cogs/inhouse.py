"""Full inhouse custom-game system: queue -> captain select -> draft -> match
-> substitutions/predictions -> NeatQueue-style result voting -> MMR/history.

All persistence, MMR/Elo math, vote-majority decisions, and parimutuel
settlement now live in inhouse-service. This cog keeps only what only the
gateway can do: the interactive captain-select/draft UI (Views/buttons),
creating/deleting match channels, moving members between voice channels,
and posting embeds -- it calls inhouse-service at each decision point and
acts on the plain-JSON result.

Simplification (unchanged from the original monolith, still worth stating
plainly): the captain-selection/draft session between "queue fills" and
"match created" lives only in this cog's in-memory DraftSession. If the
gateway restarts mid-draft, that session is lost and the 10 players need
to re-queue -- no MMR, coins, or match rows exist yet at that point.
"""
import asyncio
import logging
import random
from collections import Counter
from dataclasses import dataclass, field

import discord
from discord import app_commands
from discord.ext import commands, tasks

import config
from utils import embeds, settings_client as settings
from utils.clients import inhouse_client
from utils.permissions import is_staff, require_staff
from utils.service_client import ServiceError
from utils.views import PaginatedEmbedView

logger = logging.getLogger("bot.inhouse")


# ---------------------------------------------------------------------------
# In-memory draft session (queue -> captains -> draft -> match creation)
# ---------------------------------------------------------------------------

@dataclass
class DraftSession:
    players: list[str]
    channel_id: int
    captains: list[str] = field(default_factory=list)
    pool: list[str] = field(default_factory=list)
    team_a: list[str] = field(default_factory=list)
    team_b: list[str] = field(default_factory=list)
    draft_method: str | None = None
    snake_order: list[str] = field(default_factory=list)
    snake_index: int = 0


class CaptainMethodView(discord.ui.View):
    def __init__(self, cog: "Inhouse"):
        super().__init__(timeout=600)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        session = self.cog.session
        if session is None or str(interaction.user.id) not in session.players:
            await interaction.response.send_message("Only queued players can choose the captain method.", ephemeral=True)
            return False
        return True

    async def _pick_captains(self, interaction: discord.Interaction, captains: list[str]):
        session = self.cog.session
        session.captains = captains
        session.pool = [p for p in session.players if p not in captains]
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"Captains: <@{captains[0]}> and <@{captains[1]}>! Choose a draft method:",
            view=None,
        )
        await self.cog.start_draft_method_vote(interaction.channel, session)

    @discord.ui.button(label="Volunteer Captains", style=discord.ButtonStyle.primary)
    async def volunteer(self, interaction: discord.Interaction, button: discord.ui.Button):
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Who wants to captain? First 2 clicks!", view=VolunteerCaptainView(self.cog))

    @discord.ui.button(label="Highest MMR Captains", style=discord.ButtonStyle.secondary)
    async def highest_mmr(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.session
        result = await inhouse_client.post("/captains/highest-mmr", {"pool": session.players})
        await self._pick_captains(interaction, result["captains"])

    @discord.ui.button(label="Random Captains", style=discord.ButtonStyle.secondary)
    async def random_captains(self, interaction: discord.Interaction, button: discord.ui.Button):
        session = self.cog.session
        await self._pick_captains(interaction, random.sample(session.players, 2))


class VolunteerCaptainView(discord.ui.View):
    def __init__(self, cog: "Inhouse"):
        super().__init__(timeout=600)
        self.cog = cog
        self.volunteers: list[str] = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        session = self.cog.session
        if session is None or str(interaction.user.id) not in session.players:
            await interaction.response.send_message("Only queued players can volunteer.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Volunteer", style=discord.ButtonStyle.success)
    async def volunteer(self, interaction: discord.Interaction, button: discord.ui.Button):
        user_id = str(interaction.user.id)
        if user_id in self.volunteers:
            await interaction.response.send_message("You already volunteered.", ephemeral=True)
            return
        self.volunteers.append(user_id)
        if len(self.volunteers) < 2:
            await interaction.response.edit_message(content=f"Who wants to captain? Volunteered so far: {', '.join(f'<@{v}>' for v in self.volunteers)}")
            return

        session = self.cog.session
        session.captains = self.volunteers[:2]
        session.pool = [p for p in session.players if p not in session.captains]
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(
            content=f"Captains: <@{session.captains[0]}> and <@{session.captains[1]}>! Choose a draft method:",
            view=None,
        )
        await self.cog.start_draft_method_vote(interaction.channel, session)


class DraftMethodView(discord.ui.View):
    def __init__(self, cog: "Inhouse"):
        super().__init__(timeout=600)
        self.cog = cog

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        session = self.cog.session
        if session is None or str(interaction.user.id) not in session.captains:
            await interaction.response.send_message("Only the two captains can choose the draft method.", ephemeral=True)
            return False
        return True

    async def _lock(self, interaction: discord.Interaction, method: str):
        session = self.cog.session
        if session.draft_method is not None:
            return
        session.draft_method = method
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content=f"Draft method: **{method}**", view=None)
        await self.cog.run_draft(interaction.channel, session)

    @discord.ui.button(label="Snake Draft", style=discord.ButtonStyle.primary)
    async def snake(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._lock(interaction, "snake")

    @discord.ui.button(label="Random Teams", style=discord.ButtonStyle.secondary)
    async def random_teams(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._lock(interaction, "random")

    @discord.ui.button(label="Balanced by MMR", style=discord.ButtonStyle.secondary)
    async def balanced(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._lock(interaction, "balanced_mmr")


# Classic 2-captain snake order for an 8-player pool: A,B,B,A,A,B,B,A
# (A gets picks 1/4/5/8, B gets picks 2/3/6/7 -> 4 picks each).
SNAKE_PICK_ORDER = ["A", "B", "B", "A", "A", "B", "B", "A"]


class SnakeDraftSelect(discord.ui.Select):
    def __init__(self, cog: "Inhouse", session: DraftSession, on_turn_captain: str):
        self.cog = cog
        self.session = session
        self.on_turn_captain = on_turn_captain
        options = [discord.SelectOption(label=cog.bot.get_user(int(p)).display_name if cog.bot.get_user(int(p)) else p, value=p) for p in session.pool]
        super().__init__(placeholder="Pick a player...", options=options)

    async def callback(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.on_turn_captain:
            await interaction.response.send_message("It's not your turn to pick.", ephemeral=True)
            return
        picked = self.values[0]
        session = self.session
        session.pool.remove(picked)
        team = session.snake_order[session.snake_index]
        (session.team_a if team == "A" else session.team_b).append(picked)
        session.snake_index += 1

        board = (
            f"Team A: {', '.join(f'<@{p}>' for p in [session.captains[0]] + session.team_a)}\n"
            f"Team B: {', '.join(f'<@{p}>' for p in [session.captains[1]] + session.team_b)}"
        )
        if not session.pool:
            await interaction.response.edit_message(content=f"Draft complete!\n{board}", view=None)
            await self.cog.finalize_match(interaction.channel, session)
            return

        next_captain = session.captains[0] if session.snake_order[session.snake_index] == "A" else session.captains[1]
        view = discord.ui.View(timeout=600)
        view.add_item(SnakeDraftSelect(self.cog, session, next_captain))
        await interaction.response.edit_message(content=f"{board}\n\n<@{next_captain}>'s pick:", view=view)


class MapVoteSelect(discord.ui.Select):
    def __init__(self, map_pool: list[str]):
        options = [discord.SelectOption(label=m) for m in map_pool]
        super().__init__(placeholder="Vote for a map...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        view: MapVoteView = self.view
        view.record_vote(str(interaction.user.id), self.values[0])
        await interaction.response.send_message(f"Voted for **{self.values[0]}**.", ephemeral=True)


class MapVoteView(discord.ui.View):
    def __init__(self, players: list[str], map_pool: list[str]):
        super().__init__(timeout=config.INHOUSE_MAP_VOTE_TIMEOUT_SECONDS)
        self.players = set(players)
        self.votes: dict[str, str] = {}
        self.done = asyncio.Event()
        self.add_item(MapVoteSelect(map_pool))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) not in self.players:
            await interaction.response.send_message("Only players in this match can vote.", ephemeral=True)
            return False
        return True

    def record_vote(self, user_id: str, map_name: str):
        self.votes[user_id] = map_name
        if len(self.votes) >= len(self.players):
            self.done.set()

    async def on_timeout(self):
        self.done.set()


class FinishVoteView(discord.ui.View):
    def __init__(self, cog: "Inhouse", match_id: int, roster: dict[str, str]):
        super().__init__(timeout=config.INHOUSE_VOTING_TIMEOUT_SECONDS)
        self.cog = cog
        self.match_id = match_id
        self.roster = roster
        self.resolved = False
        self.message: discord.Message | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if str(interaction.user.id) not in self.roster:
            await interaction.response.send_message("Only players in this match can vote.", ephemeral=True)
            return False
        return True

    async def _vote(self, interaction: discord.Interaction, team: str):
        if self.resolved:
            await interaction.response.send_message("This match has already been resolved.", ephemeral=True)
            return

        result = await inhouse_client.post(f"/matches/{self.match_id}/vote", {"user_id": str(interaction.user.id), "team": team})

        # Test mode: fake players can't click a button, so auto-cast their
        # votes to match the real tester's choice, one at a time, stopping
        # as soon as a majority is reached -- avoids waiting on 9 accounts
        # that don't exist.
        if result["outcome"] == "pending" and any(uid in config.INHOUSE_TEST_PLAYER_IDS for uid in self.roster):
            for fake_id in config.INHOUSE_TEST_PLAYER_IDS:
                if fake_id not in self.roster:
                    continue
                result = await inhouse_client.post(f"/matches/{self.match_id}/vote", {"user_id": fake_id, "team": team})
                if result["outcome"] != "pending":
                    break

        votes_a, votes_b = result["votes_a"], result["votes_b"]

        if result["outcome"] == "resolved":
            winner = result["winning_team"]
            self.resolved = True
            self.stop()
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(
                content=None, embed=embeds.result_embed(self.match_id, winner, votes_a, votes_b), view=self
            )
            await self.cog.post_result_and_schedule_cleanup(self.match_id, winner)
            return

        if result["outcome"] == "disputed":
            self.resolved = True
            self.stop()
            for child in self.children:
                child.disabled = True
            await interaction.response.edit_message(content=f"Vote: A={votes_a} B={votes_b}. No majority reached.", view=self)
            await self.cog.announce_disputed(self.match_id, interaction.channel)
            return

        await interaction.response.edit_message(content=f"Vote in progress — Team A: {votes_a}, Team B: {votes_b} (need a majority)", view=self)

    @discord.ui.button(label="Team A Won", style=discord.ButtonStyle.success)
    async def team_a(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "A")

    @discord.ui.button(label="Team B Won", style=discord.ButtonStyle.danger)
    async def team_b(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._vote(interaction, "B")

    async def on_timeout(self):
        if self.resolved:
            return
        self.resolved = True
        result = await inhouse_client.post(f"/matches/{self.match_id}/timeout-check")
        if result["disputed"]:
            if self.message:
                for child in self.children:
                    child.disabled = True
                try:
                    await self.message.edit(content="Voting timed out with no majority.", view=self)
                except discord.HTTPException:
                    pass
            await self.cog.announce_disputed(self.match_id, None)


# ---------------------------------------------------------------------------
# Cog
# ---------------------------------------------------------------------------

class Inhouse(commands.Cog):
    inhouse_group = app_commands.Group(name="inhouse", description="Inhouse custom-game system")

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.session: DraftSession | None = None
        self.channel_sweep.start()

    def cog_unload(self):
        self.channel_sweep.cancel()

    # -- queue --------------------------------------------------------

    @inhouse_group.command(name="join", description="Join the inhouse queue")
    async def join(self, interaction: discord.Interaction):
        user_id = str(interaction.user.id)

        if self.session is not None and user_id in self.session.players:
            await interaction.response.send_message(embed=embeds.error_embed("You're already in an active draft."), ephemeral=True)
            return

        try:
            result = await inhouse_client.post("/queue/join", {"user_id": user_id})
        except ServiceError as e:
            error_map = {"in_active_match": "You're already in an active match.", "already_queued": "You're already in the queue."}
            await interaction.response.send_message(embed=embeds.error_embed(error_map.get(e.body.get("error"), "Something went wrong.")), ephemeral=True)
            return

        await interaction.response.send_message(f"You joined the inhouse queue ({result['count']}/{config.INHOUSE_SIZE}).")

        if result["drained_players"] is not None:
            players = result["drained_players"]
            self.session = DraftSession(players=players, channel_id=interaction.channel_id)
            mention_list = ", ".join(f"<@{p}>" for p in players)
            await interaction.channel.send(
                f"Queue full! {mention_list}\nHow should captains be chosen?", view=CaptainMethodView(self)
            )

    @inhouse_group.command(name="leave", description="Leave the inhouse queue")
    async def leave(self, interaction: discord.Interaction):
        try:
            await inhouse_client.post("/queue/leave", {"user_id": str(interaction.user.id)})
        except ServiceError:
            await interaction.response.send_message(embed=embeds.error_embed("You're not in the queue."), ephemeral=True)
            return
        await interaction.response.send_message("You left the inhouse queue.")

    @inhouse_group.command(name="status", description="Show the current inhouse queue")
    async def status(self, interaction: discord.Interaction):
        rows = await inhouse_client.get("/queue")
        names = ", ".join(f"<@{r['user_id']}>" for r in rows) or "empty"
        await interaction.response.send_message(f"Queue ({len(rows)}/{config.INHOUSE_SIZE}): {names}")

    @inhouse_group.command(name="debug-fill", description="Staff: fill the queue with fake test players (join yourself first)")
    @require_staff()
    async def debug_fill(self, interaction: discord.Interaction):
        if self.session is not None:
            await interaction.response.send_message(embed=embeds.error_embed("A draft is already in progress."), ephemeral=True)
            return

        await interaction.response.defer()
        drained = None
        for fake_id in config.INHOUSE_TEST_PLAYER_IDS:
            try:
                result = await inhouse_client.post("/queue/join", {"user_id": fake_id})
            except ServiceError:
                continue  # already queued somehow -- skip
            if result["drained_players"] is not None:
                drained = result["drained_players"]
                break

        if drained is None:
            await interaction.followup.send("Filled the queue with test players but it didn't drain -- make sure you've joined with `/inhouse join` too.")
            return

        self.session = DraftSession(players=drained, channel_id=interaction.channel_id)
        await interaction.followup.send(f"[test mode] Queue full: {', '.join(f'<@{p}>' for p in drained)}")
        await self._auto_resolve_test_draft(interaction.channel, self.session)

    async def _auto_resolve_test_draft(self, channel: discord.abc.Messageable, session: DraftSession):
        result = await inhouse_client.post("/captains/highest-mmr", {"pool": session.players})
        session.captains = result["captains"]
        session.pool = [p for p in session.players if p not in session.captains]
        session.draft_method = "balanced_mmr"
        await channel.send(f"[test mode] Captains: <@{session.captains[0]}> and <@{session.captains[1]}> -- draft method: balanced MMR")
        await self.run_draft(channel, session)

    # -- draft flow helpers --------------------------------------------

    async def start_draft_method_vote(self, channel: discord.abc.Messageable, session: DraftSession):
        await channel.send(
            f"<@{session.captains[0]}> and <@{session.captains[1]}>, choose a draft method:",
            view=DraftMethodView(self),
        )

    async def run_draft(self, channel: discord.abc.Messageable, session: DraftSession):
        if session.draft_method == "random":
            pool = session.pool[:]
            random.shuffle(pool)
            session.team_a, session.team_b = pool[:4], pool[4:]
            await channel.send(
                f"Random teams!\nTeam A: {', '.join(f'<@{p}>' for p in [session.captains[0]] + session.team_a)}\n"
                f"Team B: {', '.join(f'<@{p}>' for p in [session.captains[1]] + session.team_b)}"
            )
            await self.finalize_match(channel, session)

        elif session.draft_method == "balanced_mmr":
            result = await inhouse_client.post("/draft/balanced-mmr", {"pool": session.pool})
            session.team_a, session.team_b = result["team_a"], result["team_b"]
            await channel.send(
                f"Balanced teams (by MMR)!\nTeam A: {', '.join(f'<@{p}>' for p in [session.captains[0]] + session.team_a)}\n"
                f"Team B: {', '.join(f'<@{p}>' for p in [session.captains[1]] + session.team_b)}"
            )
            await self.finalize_match(channel, session)

        else:  # snake
            session.snake_order = SNAKE_PICK_ORDER[:]
            session.snake_index = 0
            first_captain = session.captains[0]
            view = discord.ui.View(timeout=600)
            view.add_item(SnakeDraftSelect(self, session, first_captain))
            await channel.send(f"Snake draft! <@{first_captain}>'s pick:", view=view)

    async def finalize_match(self, channel: discord.abc.Messageable, session: DraftSession):
        team_a = [session.captains[0]] + session.team_a
        team_b = [session.captains[1]] + session.team_b
        all_players = team_a + team_b

        chosen_map = await self.run_map_vote(channel, all_players)

        result = await inhouse_client.post("/matches", {
            "captain_a": session.captains[0], "captain_b": session.captains[1],
            "draft_method": session.draft_method, "team_a": team_a, "team_b": team_b,
            "map": chosen_map,
        })
        match_id = result["match_id"]

        guild = channel.guild
        text_channel, voice_a, voice_b = await self._create_match_channels(guild, match_id, team_a, team_b)
        await inhouse_client.post(f"/matches/{match_id}/channels", {
            "text_channel_id": str(text_channel.id), "voice_channel_a_id": str(voice_a.id), "voice_channel_b_id": str(voice_b.id),
        })
        await self._move_connected_players(guild, team_a, voice_a)
        await self._move_connected_players(guild, team_b, voice_b)

        await text_channel.send(embed=embeds.match_embed(match_id, team_a, team_b, "in_progress", chosen_map))
        await channel.send(f"Match #{match_id} created: {text_channel.mention}")
        self.session = None

    async def run_map_vote(self, channel: discord.abc.Messageable, players: list[str]) -> str:
        view = MapVoteView(players, config.INHOUSE_MAP_POOL)
        # Test mode: fake players can't click the dropdown, so pre-cast a
        # random vote for each so the real tester's own vote alone finishes it.
        for p in players:
            if p in config.INHOUSE_TEST_PLAYER_IDS:
                view.record_vote(p, random.choice(config.INHOUSE_MAP_POOL))

        await channel.send("🗺️ Vote for the map to play:", view=view)
        await view.done.wait()
        for child in view.children:
            child.disabled = True

        chosen = self._tally_map_vote(view.votes, config.INHOUSE_MAP_POOL)
        note = "" if view.votes else " (no votes -- picked randomly)"
        await channel.send(f"🗺️ Map picked: **{chosen}**{note}")
        return chosen

    def _tally_map_vote(self, votes: dict[str, str], map_pool: list[str]) -> str:
        if not votes:
            return random.choice(map_pool)
        counts = Counter(votes.values())
        top = max(counts.values())
        winners = [m for m, c in counts.items() if c == top]
        return random.choice(winners)

    async def _create_match_channels(self, guild: discord.Guild, match_id: int, team_a: list[str], team_b: list[str]):
        guild_settings = await settings.get(guild.id)
        category_id = guild_settings["inhouse_voice_category_id"]
        category = guild.get_channel(int(category_id)) if category_id else None
        text_channel = await guild.create_text_channel(f"inhouse-match-{match_id}", category=category)
        voice_a = await guild.create_voice_channel(f"Inhouse #{match_id} - Team A", category=category)
        voice_b = await guild.create_voice_channel(f"Inhouse #{match_id} - Team B", category=category)
        return text_channel, voice_a, voice_b

    async def _move_connected_players(self, guild: discord.Guild, players: list[str], channel: discord.VoiceChannel):
        for p in players:
            member = guild.get_member(int(p))
            if member and member.voice and member.voice.channel:
                try:
                    await member.move_to(channel)
                except discord.HTTPException:
                    logger.warning("Failed to move %s into %s", p, channel.id)

    # -- substitutions ---------------------------------------------------

    @inhouse_group.command(name="sub", description="Substitute a player mid-match")
    @app_commands.describe(match_id="The match ID", player_out="Player to remove", player_in="Player to bring in")
    async def sub(self, interaction: discord.Interaction, match_id: int, player_out: discord.Member, player_in: discord.Member):
        caller_is_staff = await is_staff(interaction)
        try:
            result = await inhouse_client.post(f"/matches/{match_id}/substitute", {
                "caller_id": str(interaction.user.id), "caller_is_staff": caller_is_staff,
                "player_out": str(player_out.id), "player_in": str(player_in.id),
            })
        except ServiceError as e:
            error_map = {
                "not_found": "Match not found.",
                "not_in_progress": "This match isn't in progress.",
                "not_authorized": "Only this match's captains or staff can substitute players.",
                "player_out_not_on_roster": f"{player_out.display_name} isn't on this match's roster.",
                "player_in_already_on_roster": f"{player_in.display_name} is already on this match's roster.",
                "player_in_already_active": f"{player_in.display_name} is already in another active match.",
            }
            await interaction.response.send_message(embed=embeds.error_embed(error_map.get(e.body.get("error"), "Something went wrong.")), ephemeral=True)
            return

        team = result["team"]
        match = await inhouse_client.get(f"/matches/{match_id}")
        guild = interaction.guild
        voice_id = match["voice_channel_a_id"] if team == "A" else match["voice_channel_b_id"]
        voice_channel = guild.get_channel(int(voice_id)) if voice_id else None
        if voice_channel:
            if player_out.voice and player_out.voice.channel and player_out.voice.channel.id == voice_channel.id:
                try:
                    await player_out.move_to(None)
                except discord.HTTPException:
                    pass
            if player_in.voice and player_in.voice.channel:
                try:
                    await player_in.move_to(voice_channel)
                except discord.HTTPException:
                    pass

        await interaction.response.send_message(f"Substituted {player_out.mention} out, {player_in.mention} in on Team {team}.")

    # -- predictions -------------------------------------------------------

    @inhouse_group.command(name="predict", description="Wager coins on which team wins an ongoing inhouse match")
    @app_commands.describe(match_id="The match ID", team="Which team you think wins", amount="How many coins to wager")
    @app_commands.choices(team=[app_commands.Choice(name="Team A", value="A"), app_commands.Choice(name="Team B", value="B")])
    async def predict(self, interaction: discord.Interaction, match_id: int, team: app_commands.Choice[str], amount: app_commands.Range[int, 1]):
        try:
            await inhouse_client.post(f"/matches/{match_id}/predict", {"user_id": str(interaction.user.id), "team": team.value, "amount": amount})
        except ServiceError as e:
            error_map = {
                "not_found": "Match not found.",
                "predictions_closed": "Predictions are closed for this match.",
                "cannot_predict_own_match": "You can't predict on a match you're playing in.",
                "insufficient_balance": "You don't have enough coins for that wager.",
                "duplicate_prediction": "You've already placed a prediction on this match.",
            }
            await interaction.response.send_message(embed=embeds.error_embed(error_map.get(e.body.get("error"), "Something went wrong.")), ephemeral=True)
            return

        guild_settings = await settings.get(interaction.guild_id)
        await interaction.response.send_message(f"Predicted **Team {team.value}** for **{amount:,} {guild_settings['currency_name']}** on match #{match_id}.")

    # -- finish / voting -----------------------------------------------

    @inhouse_group.command(name="finish", description="Start result voting for a match")
    @app_commands.describe(match_id="The match ID")
    async def finish(self, interaction: discord.Interaction, match_id: int):
        try:
            result = await inhouse_client.post(f"/matches/{match_id}/start-voting", {"user_id": str(interaction.user.id)})
        except ServiceError as e:
            error_map = {
                "not_found": "Match not found.",
                "not_in_progress": "This match isn't in progress.",
                "not_on_roster": "Only players in this match can start result voting.",
            }
            await interaction.response.send_message(embed=embeds.error_embed(error_map.get(e.body.get("error"), "Something went wrong.")), ephemeral=True)
            return

        roster = result["roster"]
        team_a = [uid for uid, t in roster.items() if t == "A"]
        team_b = [uid for uid, t in roster.items() if t == "B"]
        embed = embeds.match_embed(match_id, team_a, team_b, "voting")
        embed.description = "Vote for which team won. Resolves automatically once one side gets a majority."
        view = FinishVoteView(self, match_id, roster)
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    async def post_result_and_schedule_cleanup(self, match_id: int, winning_team: str):
        match = await inhouse_client.get(f"/matches/{match_id}")
        guild_channel = self.bot.get_channel(int(match["text_channel_id"])) if match["text_channel_id"] else None
        if guild_channel:
            try:
                await guild_channel.send(f"Match #{match_id} resolved: **Team {winning_team}** wins. This channel will be cleaned up shortly.")
            except discord.HTTPException:
                pass
        asyncio.create_task(self._delete_match_channels_after_delay(match_id, config.INHOUSE_CHANNEL_DELETE_GRACE_SECONDS))

    async def announce_disputed(self, match_id: int, channel: discord.abc.Messageable | None):
        match = await inhouse_client.get(f"/matches/{match_id}")
        target_channel = channel or (self.bot.get_channel(int(match["text_channel_id"])) if match["text_channel_id"] else None)
        if target_channel:
            guild_settings = await settings.get(target_channel.guild.id)
            staff_role_id = guild_settings["inhouse_staff_role_id"]
            role_mention = f"<@&{staff_role_id}>" if staff_role_id else "Staff"
            await target_channel.send(f"{role_mention} match #{match_id} is disputed (no vote majority) — please review with `/inhouse override`.")

    async def _delete_match_channels_after_delay(self, match_id: int, delay: int):
        await asyncio.sleep(delay)
        await self._delete_match_channels(match_id)

    async def _delete_match_channels(self, match_id: int):
        match = await inhouse_client.get(f"/matches/{match_id}")
        for col in ("text_channel_id", "voice_channel_a_id", "voice_channel_b_id"):
            channel_id = match.get(col)
            if not channel_id:
                continue
            channel = self.bot.get_channel(int(channel_id))
            if channel:
                try:
                    await channel.delete(reason=f"Inhouse match #{match_id} cleanup")
                except discord.HTTPException:
                    logger.warning("Failed to delete channel %s for match %s", channel_id, match_id)

    # -- overrides / cancel / flags --------------------------------------

    @inhouse_group.command(name="override", description="Staff: set/correct the winner of a match")
    @app_commands.describe(match_id="The match ID", winning_team="The corrected winning team")
    @app_commands.choices(winning_team=[app_commands.Choice(name="Team A", value="A"), app_commands.Choice(name="Team B", value="B")])
    @require_staff()
    async def override(self, interaction: discord.Interaction, match_id: int, winning_team: app_commands.Choice[str]):
        await interaction.response.defer()
        try:
            await inhouse_client.post(f"/matches/{match_id}/override", {"winning_team": winning_team.value, "reported_by": str(interaction.user.id)})
        except ServiceError:
            await interaction.followup.send(embed=embeds.error_embed("Match not found."))
            return
        await interaction.followup.send(f"Match #{match_id} corrected: **Team {winning_team.value}** now wins.")

    @inhouse_group.command(name="cancel", description="Staff: cancel a match with no MMR/coin changes")
    @app_commands.describe(match_id="The match ID")
    @require_staff()
    async def cancel(self, interaction: discord.Interaction, match_id: int):
        try:
            await inhouse_client.post(f"/matches/{match_id}/cancel")
        except ServiceError as e:
            error_map = {"not_found": "Match not found.", "already_completed": "This match is already completed — use `/inhouse override` instead."}
            await interaction.response.send_message(embed=embeds.error_embed(error_map.get(e.body.get("error"), "Something went wrong.")), ephemeral=True)
            return
        await interaction.response.send_message(f"Match #{match_id} cancelled. No MMR/coin changes were made.")
        asyncio.create_task(self._delete_match_channels_after_delay(match_id, config.INHOUSE_CHANNEL_DELETE_GRACE_SECONDS))

    @inhouse_group.command(name="flags", description="Show flagged (against-own-team) votes for a match")
    @app_commands.describe(match_id="The match ID")
    async def flags(self, interaction: discord.Interaction, match_id: int):
        flagged = await inhouse_client.get(f"/matches/{match_id}/flags")
        if not flagged:
            await interaction.response.send_message(f"No flagged votes for match #{match_id}.")
            return
        lines = [f"<@{v['user_id']}> (Team {v['team']}) voted for Team {v['voted_team']}" for v in flagged]
        await interaction.response.send_message("Flagged votes (informational only, not auto-punished):\n" + "\n".join(lines))

    # -- stats / leaderboard / history -----------------------------------

    @inhouse_group.command(name="leaderboard", description="Top 10 inhouse MMR")
    async def leaderboard(self, interaction: discord.Interaction):
        rows = await inhouse_client.get("/leaderboard")
        await interaction.response.send_message(embed=embeds.inhouse_leaderboard_embed(rows, interaction.guild))

    @inhouse_group.command(name="stats", description="Show a user's inhouse stats")
    @app_commands.describe(user="Whose stats to show (defaults to you)")
    async def stats(self, interaction: discord.Interaction, user: discord.Member | None = None):
        target = user or interaction.user
        result = await inhouse_client.get(f"/stats/{target.id}")
        row = result["mmr"]
        total = row["wins"] + row["losses"]
        if total == 0:
            await interaction.response.send_message(f"{target.display_name} hasn't played an inhouse match yet.")
            return
        winrate = (row["wins"] / total * 100) if total else 0.0
        recent_str = ", ".join("W" if r["team"] == r["winning_team"] else "L" for r in result["recent"]) or "—"
        embed = discord.Embed(title=f"{target.display_name}'s Inhouse Stats", color=discord.Color.blurple())
        embed.add_field(name="MMR", value=str(row["mmr"]))
        embed.add_field(name="Record", value=f"{row['wins']}W / {row['losses']}L ({winrate:.0f}%)")
        embed.add_field(name="Last 5", value=recent_str, inline=False)
        await interaction.response.send_message(embed=embed)

    @inhouse_group.command(name="history", description="Show a user's inhouse match history")
    @app_commands.describe(user="Whose history to show (defaults to you)")
    async def history(self, interaction: discord.Interaction, user: discord.Member | None = None):
        target = user or interaction.user
        rows = await inhouse_client.get(f"/history/{target.id}")
        if not rows:
            await interaction.response.send_message(f"{target.display_name} has no match history yet.")
            return

        pages = []
        page_size = 5
        for i in range(0, len(rows), page_size):
            chunk = rows[i:i + page_size]
            embed = discord.Embed(title=f"{target.display_name}'s Match History", color=discord.Color.blurple())
            for m in chunk:
                result = "Cancelled" if m["status"] == "cancelled" else f"Winner: Team {m['winning_team']}"
                embed.add_field(name=f"Match #{m['match_id']}", value=result, inline=False)
            embed.set_footer(text=f"Page {i // page_size + 1}/{(len(rows) - 1) // page_size + 1}")
            pages.append(embed)

        if len(pages) == 1:
            await interaction.response.send_message(embed=pages[0])
        else:
            await interaction.response.send_message(embed=pages[0], view=PaginatedEmbedView(pages))

    # -- cleanup sweep ------------------------------------------------------

    @tasks.loop(minutes=config.INHOUSE_CHANNEL_SWEEP_INTERVAL_MINUTES)
    async def channel_sweep(self):
        rows = await inhouse_client.get("/sweep-candidates", params={"max_age_hours": config.INHOUSE_CHANNEL_MAX_AGE_HOURS})
        for match in rows:
            await self._delete_match_channels(match["match_id"])
            await inhouse_client.post(f"/matches/{match['match_id']}/clear-channels")

    @channel_sweep.before_loop
    async def before_channel_sweep(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Inhouse(bot))
