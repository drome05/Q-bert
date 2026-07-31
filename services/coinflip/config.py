import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")
