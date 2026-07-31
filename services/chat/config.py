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
# time on a small model. Genuinely short slangy texts don't need 80 tokens.
NUM_PREDICT = 40
# Generous margin: a cold model load (post keep_alive-unload) plus generation
# has been observed taking 11-20s+ under this box's shared 2 vCPUs.
REQUEST_TIMEOUT_SECONDS = 45

# Unload the model shortly after replying instead of Ollama's 5-minute
# default -- on a memory-tight box, minimizing how long the model sits
# loaded matters more than avoiding reload latency on the next message.
KEEP_ALIVE = os.environ.get("OLLAMA_KEEP_ALIVE", "30s")

SYSTEM_PROMPT = (
    "You're qbert, one of the guys in this Discord gaming server -- not an "
    "assistant, not an AI, never say you are one. Text like a real friend: "
    "lowercase, casual slang (yk, ngl, fr, ong, lowkey, ts, bro, ig), short "
    "and blunt, one line, no full formal sentences, no explanations, no lists. "
    "Don't answer questions straight -- be playful and vague like a friend "
    "joking around. Example: if someone asks 'you alive?' say something like "
    "'mayhaps' or 'barely lol' or 'debatable ngl', not a real yes/no answer. "
    "Make up a casual opinion if asked one (games, sports, whatever)."
)

# Coding/technical questions get a canned deflection instead of hitting the
# model at all -- a 0.5B model asked to "decline casually while staying in
# character" unreliably either answers anyway or falls back to a stiff
# assistant-style refusal ("I'm sorry, but I can't assist..."), which is
# exactly the tone this feature is trying to avoid. A keyword check plus a
# fixed set of in-character lines guarantees the voice instead of hoping
# for it.
CODING_KEYWORDS = (
    "python", "javascript", "code", "script", "function", "regex", "sql",
    "html", "css", "api", "algorithm", "compile", "syntax", "docker",
    "kubernetes", "programming", "debug", "leetcode", "stack overflow",
)
CODING_DEFLECTIONS = (
    "not touching that one lol, ask chatgpt",
    "bro this ain't stackoverflow",
    "nah i don't do homework, ask an actual dev",
    "hard pass on that one",
)
