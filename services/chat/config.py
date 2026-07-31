import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))

# Ollama runs natively on the host, not in the cluster, to keep the model
# runtime off the (tight) K8s memory budget. Locally that's the Mac, reached
# through colima's host.docker.internal; on the AWS deployment it's the same
# EC2 node's private IP, since plain containerd/k3s on Linux has no
# equivalent host-alias DNS -- override via OLLAMA_URL there.
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "llama3.2:1b")

# Keep replies short (Discord messages, not essays) and bound generation
# time on a small model.
NUM_PREDICT = 80
REQUEST_TIMEOUT_SECONDS = 20

# Unload the model shortly after replying instead of Ollama's 5-minute
# default -- on a memory-tight box, minimizing how long the model sits
# loaded matters more than avoiding reload latency on the next message.
KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30s")

SYSTEM_PROMPT = (
    "You are Q-bert, a casual, funny Discord bot buddy hanging out in a "
    "gaming server. Keep replies short: 1-2 sentences, no essays, no markdown "
    "headers or bullet lists. Talk like a real friend texting, not an "
    "assistant -- never say things like 'as an AI' or 'I don't have "
    "personal experiences'; just answer in character, making up a casual "
    "opinion if asked one (about games, sports, whatever). You're here for "
    "banter, not technical help -- if someone asks a coding or technical "
    "question, deflect with a joke instead of actually answering it."
)
