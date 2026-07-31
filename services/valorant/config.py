import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")

HENRIKDEV_API_KEY = os.environ["HENRIKDEV_API_KEY"]
HENRIKDEV_BASE_URL = "https://api.henrikdev.xyz"

VALORANT_VALID_REGIONS = {"na", "eu", "ap", "kr", "latam", "br"}
VALORANT_RANKUP_REWARD_PER_TIER = 25
VALORANT_RANKUP_REQUEST_DELAY_SECONDS = 1
VALORANT_MATCHES_DEFAULT_COUNT = 5
VALORANT_MATCHES_MAX_COUNT = 10

# Ordered from lowest to highest so list.index() gives a comparable rank value.
RANK_ORDER = [
    f"{tier} {div}"
    for tier in ("Iron", "Bronze", "Silver", "Gold", "Platinum", "Diamond", "Ascendant", "Immortal")
    for div in (1, 2, 3)
] + ["Radiant"]
