"""One shared ServiceClient per backend service, started/closed once from
bot.py's setup_hook/on_close so every cog reuses the same aiohttp sessions.
"""
import config
from utils.service_client import ServiceClient

db_client = ServiceClient(config.DB_SERVICE_URL)
blackjack_client = ServiceClient(config.BLACKJACK_SERVICE_URL)
coinflip_client = ServiceClient(config.COINFLIP_SERVICE_URL)
slots_client = ServiceClient(config.SLOTS_SERVICE_URL)
economy_client = ServiceClient(config.ECONOMY_SERVICE_URL)
valorant_client = ServiceClient(config.VALORANT_SERVICE_URL)
inhouse_client = ServiceClient(config.INHOUSE_SERVICE_URL)
twitch_client = ServiceClient(config.TWITCH_SERVICE_URL)

ALL_CLIENTS = [db_client, blackjack_client, coinflip_client, slots_client, economy_client, inhouse_client, valorant_client, twitch_client]


async def start_all():
    for client in ALL_CLIENTS:
        await client.start()


async def close_all():
    for client in ALL_CLIENTS:
        await client.close()
