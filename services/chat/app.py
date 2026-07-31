"""chat-service: casual banter via a local Ollama model. No persistence,
no conversation history -- each message is answered independently. Ollama
runs natively on the Mac host (see config.py for why), so this service is
the only thing that talks to it; the gateway never calls Ollama directly.
"""
import asyncio
import logging
import random

import aiohttp
from aiohttp import web

import config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("chat-service")

routes = web.RouteTableDef()
session: aiohttp.ClientSession | None = None


@routes.get("/healthz")
async def healthz(request):
    return web.json_response({"status": "ok"})


@routes.post("/chat")
async def chat(request):
    body = await request.json()
    message = body["message"]

    if any(kw in message.lower() for kw in config.CODING_KEYWORDS):
        return web.json_response({"reply": random.choice(config.CODING_DEFLECTIONS)})

    payload = {
        "model": config.OLLAMA_MODEL,
        "prompt": message,
        "system": config.SYSTEM_PROMPT,
        "stream": False,
        "keep_alive": config.KEEP_ALIVE,
        "options": {"num_predict": config.NUM_PREDICT},
    }
    try:
        async with session.post(
            f"{config.OLLAMA_URL}/api/generate", json=payload, timeout=aiohttp.ClientTimeout(total=config.REQUEST_TIMEOUT_SECONDS)
        ) as resp:
            if resp.status != 200:
                text = await resp.text()
                logger.warning("Ollama returned %s: %s", resp.status, text[:200])
                return web.json_response({"error": "unavailable"}, status=503)
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("Ollama request failed: %s", e)
        return web.json_response({"error": "unavailable"}, status=503)

    reply = data.get("response", "").strip()
    if not reply:
        return web.json_response({"error": "unavailable"}, status=503)
    return web.json_response({"reply": reply})


async def on_startup(app):
    global session
    session = aiohttp.ClientSession()
    logger.info("chat-service ready, OLLAMA_URL=%s, model=%s", config.OLLAMA_URL, config.OLLAMA_MODEL)


async def on_cleanup(app):
    if session:
        await session.close()


def create_app():
    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


if __name__ == "__main__":
    web.run_app(create_app(), port=config.PORT)
