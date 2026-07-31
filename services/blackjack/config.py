import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")

BLACKJACK_PAYOUT_MULTIPLIER = 2.0
BLACKJACK_NATURAL_PAYOUT_MULTIPLIER = 2.5
BLACKJACK_DEALER_STANDS_ON = 17
