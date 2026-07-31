"""Gateway config: only what the process holding the Discord connection
needs. Game rules, cooldowns, MMR math, etc. all moved into their owning
backend service's own config.py.
"""
import os

from dotenv import load_dotenv

load_dotenv()

# --- Secrets / IDs (env-backed) ---
DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]

# Optional: a specific guild to instant-sync commands to during active
# development (global sync alone takes ~1hr to propagate after a command
# *definition* changes). Commands still work in every guild either way --
# this only affects how fast a definition change shows up in this one guild.
_dev_guild_raw = os.environ.get("DEV_GUILD_ID")
DEV_GUILD_ID = int(_dev_guild_raw) if _dev_guild_raw and _dev_guild_raw.isdigit() else None

# --- Health check server (no other traffic hits the gateway, so this is
# only for K8s liveness/readiness probes) ---
HEALTH_PORT = int(os.environ.get("HEALTH_PORT", "8080"))

# --- Backend service URLs (ClusterIP DNS names in K8s; override for local dev) ---
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")
BLACKJACK_SERVICE_URL = os.environ.get("BLACKJACK_SERVICE_URL", "http://blackjack-service.casino.svc.cluster.local")
COINFLIP_SERVICE_URL = os.environ.get("COINFLIP_SERVICE_URL", "http://coinflip-service.casino.svc.cluster.local")
SLOTS_SERVICE_URL = os.environ.get("SLOTS_SERVICE_URL", "http://slots-service.casino.svc.cluster.local")
ECONOMY_SERVICE_URL = os.environ.get("ECONOMY_SERVICE_URL", "http://economy-service.economy.svc.cluster.local")
VALORANT_SERVICE_URL = os.environ.get("VALORANT_SERVICE_URL", "http://valorant-service.valorant.svc.cluster.local")
INHOUSE_SERVICE_URL = os.environ.get("INHOUSE_SERVICE_URL", "http://inhouse-service.inhouse.svc.cluster.local")
TWITCH_SERVICE_URL = os.environ.get("TWITCH_SERVICE_URL", "http://twitch-service.twitch.svc.cluster.local")

COINFLIP_TIMEOUT_SECONDS = 5 * 60  # UI-only concern (how long the Accept/Decline view stays up), stays in the gateway

# Inhouse UI/scheduling constants -- business rules (K-factor, rewards, MMR
# starting value, etc.) live in services/inhouse/config.py instead.
INHOUSE_SIZE = 10  # display only ("queue (7/10)"); the actual drain threshold lives in inhouse-service
INHOUSE_VOTING_TIMEOUT_SECONDS = 10 * 60
INHOUSE_CHANNEL_SWEEP_INTERVAL_MINUTES = 30
INHOUSE_CHANNEL_MAX_AGE_HOURS = 3
INHOUSE_CHANNEL_DELETE_GRACE_SECONDS = 30

# Current Valorant competitive map pool -- Riot rotates this periodically,
# so update this list by hand when maps get added/removed from rotation.
INHOUSE_MAP_POOL = ["Ascent", "Bind", "Haven", "Icebox", "Lotus", "Pearl", "Split", "Sunset", "Abyss"]
INHOUSE_MAP_VOTE_TIMEOUT_SECONDS = 2 * 60

# Fake player IDs for /inhouse debug-fill -- small sequential ints, guaranteed
# to never collide with a real Discord snowflake (always 17+ digits).
INHOUSE_TEST_PLAYER_IDS = [str(i) for i in range(1, 10)]
