import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")

INHOUSE_SIZE = 10
INHOUSE_STARTING_MMR = 1000
INHOUSE_ELO_K_FACTOR = 32
INHOUSE_FINISH_MAJORITY_VOTES = 6  # out of 10
INHOUSE_WIN_REWARD = 50
INHOUSE_LOSS_REWARD = 10

# Classic 2-captain snake order for an 8-player pool: A,B,B,A,A,B,B,A
# (A gets picks 1/4/5/8, B gets picks 2/3/6/7 -> 4 picks each).
SNAKE_PICK_ORDER = ["A", "B", "B", "A", "A", "B", "B", "A"]
