import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")

DAILY_AMOUNT = 100
WEEKLY_AMOUNT = 500
MONTHLY_AMOUNT = 2000

DAILY_COOLDOWN_SECONDS = 24 * 60 * 60
WEEKLY_COOLDOWN_SECONDS = 7 * 24 * 60 * 60
MONTHLY_COOLDOWN_SECONDS = 30 * 24 * 60 * 60

PERIODS = {
    "daily": (DAILY_AMOUNT, DAILY_COOLDOWN_SECONDS, "last_daily"),
    "weekly": (WEEKLY_AMOUNT, WEEKLY_COOLDOWN_SECONDS, "last_weekly"),
    "monthly": (MONTHLY_AMOUNT, MONTHLY_COOLDOWN_SECONDS, "last_monthly"),
}
