import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")

TWITCH_CLIENT_ID = os.environ.get("TWITCH_CLIENT_ID") or None
TWITCH_CLIENT_SECRET = os.environ.get("TWITCH_CLIENT_SECRET") or None
TWITCH_HELIX_BASE_URL = "https://api.twitch.tv/helix"
TWITCH_OAUTH_TOKEN_URL = "https://id.twitch.tv/oauth2/token"
