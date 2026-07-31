"""Music cog: ephemeral voice-channel playback via yt-dlp + ffmpeg, the
same mechanism nearly every Discord music bot (including FredBoat) uses
-- audio is streamed live into the channel and never saved to disk or
handed to the user as a file.

Runs in the gateway itself rather than a backend service: only the
process holding the Discord gateway connection can open the voice UDP
socket, and per-guild queue state is small/ephemeral (no need to
survive a restart), so splitting it out would add HTTP round-trips for
no real benefit -- same reasoning as why /settings never got its own
service.
"""
import asyncio
import logging
from collections import deque
from dataclasses import dataclass

import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands, tasks

logger = logging.getLogger("bot.music")

YTDLP_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "default_search": "ytsearch1",
    "quiet": True,
    "no_warnings": True,
}
FFMPEG_BEFORE_OPTS = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
FFMPEG_OPTS = "-vn"
IDLE_DISCONNECT_MINUTES = 5


@dataclass
class Track:
    title: str
    url: str            # resolved direct stream URL, passed to ffmpeg
    webpage_url: str     # original page URL, for display only
    requested_by: int
    duration: int | None


class GuildPlayer:
    def __init__(self):
        self.queue: deque[Track] = deque()
        self.current: Track | None = None
        self.voice_client: discord.VoiceClient | None = None
        self.idle_since: float | None = None


class Music(commands.Cog):
    music_group = app_commands.Group(name="music", description="Music playback commands", guild_only=True)

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.players: dict[int, GuildPlayer] = {}

    async def cog_load(self):
        self.idle_check.start()

    async def cog_unload(self):
        self.idle_check.cancel()

    def _get_player(self, guild_id: int) -> GuildPlayer:
        if guild_id not in self.players:
            self.players[guild_id] = GuildPlayer()
        return self.players[guild_id]

    async def _resolve(self, query: str, requested_by: int) -> Track:
        loop = asyncio.get_running_loop()

        def extract():
            with yt_dlp.YoutubeDL(YTDLP_OPTS) as ydl:
                return ydl.extract_info(query, download=False)

        info = await loop.run_in_executor(None, extract)
        if "entries" in info:
            info = info["entries"][0]
        return Track(
            title=info.get("title", "Unknown"),
            url=info["url"],
            webpage_url=info.get("webpage_url", query),
            requested_by=requested_by,
            duration=info.get("duration"),
        )

    def _play_next(self, guild_id: int):
        player = self.players.get(guild_id)
        if player is None or player.voice_client is None:
            return
        if not player.queue:
            player.current = None
            player.idle_since = asyncio.get_event_loop().time()
            return
        player.idle_since = None
        track = player.queue.popleft()
        player.current = track
        source = discord.FFmpegPCMAudio(track.url, before_options=FFMPEG_BEFORE_OPTS, options=FFMPEG_OPTS)

        def after(error):
            if error:
                logger.warning("Playback error in guild %s: %s", guild_id, error)
            asyncio.run_coroutine_threadsafe(self._advance(guild_id), self.bot.loop)

        player.voice_client.play(source, after=after)

    async def _advance(self, guild_id: int):
        self._play_next(guild_id)

    @music_group.command(name="play", description="Play a song (search terms or a direct URL)")
    @app_commands.describe(query="Search terms or a direct URL")
    async def play(self, interaction: discord.Interaction, query: str):
        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message("Join a voice channel first.", ephemeral=True)
            return
        await interaction.response.defer()

        player = self._get_player(interaction.guild_id)
        channel = interaction.user.voice.channel
        if player.voice_client is None or not player.voice_client.is_connected():
            player.voice_client = await channel.connect()
        elif player.voice_client.channel != channel:
            await player.voice_client.move_to(channel)

        try:
            track = await self._resolve(query, interaction.user.id)
        except Exception:
            logger.exception("yt-dlp resolution failed for query %r", query)
            await interaction.followup.send("Couldn't find or load that track. Try a different search or URL.")
            return

        player.queue.append(track)
        player.idle_since = None
        if player.voice_client.is_playing() or player.voice_client.is_paused():
            await interaction.followup.send(f"Queued **{track.title}**.")
        else:
            self._play_next(interaction.guild_id)
            await interaction.followup.send(f"▶️ Now playing **{track.title}**.")

    @music_group.command(name="skip", description="Skip the current song")
    async def skip(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if player is None or player.voice_client is None or not player.voice_client.is_playing():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        player.voice_client.stop()  # triggers the `after` callback, which advances the queue
        await interaction.response.send_message("Skipped.")

    @music_group.command(name="pause", description="Pause playback")
    async def pause(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if player is None or player.voice_client is None or not player.voice_client.is_playing():
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        player.voice_client.pause()
        await interaction.response.send_message("Paused.")

    @music_group.command(name="resume", description="Resume playback")
    async def resume(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if player is None or player.voice_client is None or not player.voice_client.is_paused():
            await interaction.response.send_message("Nothing is paused.", ephemeral=True)
            return
        player.voice_client.resume()
        await interaction.response.send_message("Resumed.")

    @music_group.command(name="stop", description="Stop playback and clear the queue (stays in the voice channel)")
    async def stop(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if player is None or player.voice_client is None:
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)
            return
        player.queue.clear()
        player.current = None
        player.voice_client.stop()
        await interaction.response.send_message("Stopped and cleared the queue.")

    @music_group.command(name="leave", description="Leave the voice channel")
    async def leave(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if player is None or player.voice_client is None:
            await interaction.response.send_message("Not connected to a voice channel.", ephemeral=True)
            return
        player.queue.clear()
        player.current = None
        await player.voice_client.disconnect()
        player.voice_client = None
        await interaction.response.send_message("Left the voice channel.")

    @music_group.command(name="queue", description="Show the current queue")
    async def queue(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if player is None or (player.current is None and not player.queue):
            await interaction.response.send_message("Nothing is queued.", ephemeral=True)
            return
        embed = discord.Embed(title="🎵 Music Queue", color=discord.Color.purple())
        if player.current:
            embed.add_field(name="Now Playing", value=f"{player.current.title} (<@{player.current.requested_by}>)", inline=False)
        if player.queue:
            lines = [f"{i}. {t.title} (<@{t.requested_by}>)" for i, t in enumerate(player.queue, start=1)]
            embed.add_field(name="Up Next", value="\n".join(lines[:10]), inline=False)
        await interaction.response.send_message(embed=embed)

    @music_group.command(name="nowplaying", description="Show the currently playing song")
    async def nowplaying(self, interaction: discord.Interaction):
        player = self.players.get(interaction.guild_id)
        if player is None or player.current is None:
            await interaction.response.send_message("Nothing is playing.", ephemeral=True)
            return
        await interaction.response.send_message(f"🎵 Now playing **{player.current.title}** (<{player.current.webpage_url}>)")

    @tasks.loop(minutes=1)
    async def idle_check(self):
        now = asyncio.get_event_loop().time()
        for player in list(self.players.values()):
            if player.voice_client is None:
                continue
            alone = len(player.voice_client.channel.members) <= 1  # just the bot itself
            idle_timeout = player.idle_since is not None and (now - player.idle_since) >= IDLE_DISCONNECT_MINUTES * 60
            if alone or idle_timeout:
                await player.voice_client.disconnect()
                player.voice_client = None
                player.queue.clear()
                player.current = None

    @idle_check.before_loop
    async def before_idle_check(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Music(bot))
