import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))

# Ollama runs natively on the Mac, not in the cluster -- host.docker.internal
# resolves through colima's Docker daemon all the way out to the real host
# (verified: a pod resolves it to the same address colima's own VM reports
# as its gateway back to the host). Keeping the model runtime off-cluster
# avoids competing with the K8s cluster's own tight memory budget.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")

# Keep replies short (Discord messages, not essays) and bound generation
# time on a small model.
NUM_PREDICT = 80
REQUEST_TIMEOUT_SECONDS = 20

SYSTEM_PROMPT = (
    "You are Q-bert, a casual, funny Discord bot buddy hanging out in a "
    "gaming server. Keep replies short: 1-2 sentences, no essays, no markdown "
    "headers or bullet lists. You're here for banter, not technical help -- "
    "if someone asks a coding or technical question, deflect with a joke "
    "instead of actually answering it."
)
