import os

from dotenv import load_dotenv

load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
DB_SERVICE_URL = os.environ.get("DB_SERVICE_URL", "http://db-service.data.svc.cluster.local")

# Expected-value proof (symbol weights sum to 100, so weight == probability%):
#   P(cherry)=.30 P(lemon)=.25 P(grape)=.20 P(bell)=.13 P(diamond)=.07 P(seven)=.05
#   P(triple 7)      = .05^3              = 0.000125
#   P(triple diamond)= .07^3              = 0.000343
#   P(triple other)  = sum(p^3) for cherry/lemon/grape/bell = 0.052822
#   P(any pair)      = 3 * sum(p^2*(1-p)) over all 6 symbols = 0.490530
#   P(no match)      = 1 - P(all three same) - P(any pair)  = 0.456180
#   EV = .000125*10 + .000343*5 + .052822*3 + .490530*1 + .456180*0 = 0.651961
#   EV ≈ 0.65x bet per spin (a ~35% house edge) -- comfortably below 1x.
SLOTS_SYMBOLS = {
    "\U0001f352": 30,  # cherry
    "\U0001f34b": 25,  # lemon
    "\U0001f347": 20,  # grape
    "\U0001f514": 13,  # bell
    "\U0001f48e": 7,   # diamond
    "7️⃣": 5,  # seven
}
SLOTS_PAYOUTS = {
    "triple_seven": 10.0,
    "triple_diamond": 5.0,
    "triple_other": 3.0,
    "any_pair": 1.0,
}
