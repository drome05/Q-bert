# Discord Inhouse/Economy Bot

A single-guild Discord bot with five systems: coin economy ("Pink Slips" by
default), casino games (Blackjack/Coinflip/Slots), a Valorant rank/match
tracker, a full inhouse custom-game system (queue, draft, MMR, predictions,
NeatQueue-style result voting), and Twitch live announcements.

## Architecture: gateway + backend microservices

This runs as **9 pods across 7 Kubernetes namespaces**, not one process:

```
Discord  <-->  gateway (ns: gateway)
                  |  the ONLY thing that imports discord.py / holds the token
                  |  cogs/*.py are thin HTTP clients -- no business logic, no DB access
                  v
   ns: casino (3 pods)         economy-service   valorant-service   inhouse-service   twitch-service
   blackjack-service           (ns: economy)     (ns: valorant)     (ns: inhouse)     (ns: twitch)
   coinflip-service            balance/daily/    HenrikDev calls,   queue/draft/match/  Twitch Helix calls,
   slots-service               weekly/give       rank-index math    voting/elo/predict  live-poll check
   (each its own pod --
   one game, one process)
                  \_____________|______________|______________|____________/
                                        |
                                  db-service (ns: data)
                              owns bot.db + the PVC -- the ONLY
                              thing that touches SQLite directly
```

Casino is split one level finer than the other domains: blackjack, coinflip,
and slots are each their own pod (`blackjack-service`, `coinflip-service`,
`slots-service`, all in the `casino` namespace) rather than one combined
`casino-service`. Worth being honest about why the *other* namespaces
weren't split the same way: blackjack/coinflip/slots have no real
independent scaling/failure/ownership reasons to be separate services in a
production sense — they change together and share the same "touch
db-service for a balance" pattern. The split here is deliberately for K8s
practice (more Services/Deployments/DNS reps), not because it's the
textbook-correct service boundary — a distinction worth knowing even while
choosing to do it anyway.

Two hard constraints shaped this design:
1. **Only one process can ever hold the Discord gateway connection** for a
   given token. Running each system as its own independent bot would
   double-connect the gateway and cause duplicate slash-command responses
   (this actually happened during development). So business-logic services
   have no Discord token at all — the gateway calls them over plain HTTP/JSON
   and renders the result itself.
2. **SQLite can't be written by multiple processes over the network**, and
   Kubernetes PVCs here are `ReadWriteOnce` (can't even mount the same volume
   in two pods at once). So `db-service` is the sole owner of `bot.db`; every
   other service calls it over the cluster's internal network instead of
   importing a database module directly.

Discord-specific side effects that only the gateway can perform (creating
inhouse match channels, moving members between voice channels, posting
announcement embeds) stay in the gateway's cogs even when the underlying
decision (who's the winner, did a stream just start) is made by a backend
service — the gateway always initiates calls to services, never the other
way around, so no service needs to expose anything back to Discord itself.

Internal calls are plain `aiohttp.web` JSON over ClusterIP Services
(`http://<service>.<namespace>.svc.cluster.local`), no auth between them —
reasonable for internal-only, not-internet-exposed services on a personal
learning cluster; a deliberate simplification, not an oversight.

## Configuring the bot: `/settings`

Most per-server tunables are configured **in Discord**, not by editing
files — run `/settings show` (staff/admin only) to see current values, and:

- `/settings currency name: emoji:` — rename the coin currency.
- `/settings valorant-channel channel:` — where rank-up announcements post.
- `/settings staff-role role:` — the staff/mod role: allowed to use
  `/inhouse override`/`cancel`, pinged on disputed inhouse matches, and
  allowed to use the Twitch moderation commands below.
- `/settings voice-category category:` — where temporary inhouse match
  voice/text channels are created.
- `/settings twitch-channel channel:` — where Twitch live announcements post.

These live in `db-service`'s `guild_settings` table, not `.env`, so changing
them doesn't require a restart. Secrets (`DISCORD_BOT_TOKEN`,
`HENRIKDEV_API_KEY`, `TWITCH_CLIENT_ID`/`SECRET`) and boot-time config
(`GUILD_ID`, `DB_PATH`) stay env-only. The corresponding
`VALORANT_UPDATES_CHANNEL_ID`/`INHOUSE_STAFF_ROLE_ID`/
`INHOUSE_VOICE_CATEGORY_ID` env vars only matter as **first-run seed
values** — `db-service` copies them into `guild_settings` the first time it
sees a guild, and `/settings` takes over from there.

## Twitch live announcements

Users self-link with `/twitch link username:`, and the bot posts an
announcement when they go live (checked every few minutes). Staff (the role
set via `/settings staff-role`) get `/twitch list`, `/twitch unlink-user
member:`, and `/twitch announce member:` (check right now and post
immediately if they're live).

This needs its own free credentials:

1. Go to https://dev.twitch.tv/console/apps → **Register Your Application**.
2. Name it anything, OAuth Redirect URL `https://localhost` (required by the
   form, unused — this only does the app-level client-credentials grant,
   never a per-user login), category "Chat Bot".
3. Copy the **Client ID**, click **New Secret** for the **Client Secret**.
4. `kubectl create secret generic twitch-secrets -n twitch --from-literal=TWITCH_CLIENT_ID=xxx --from-literal=TWITCH_CLIENT_SECRET=xxx`

Until that secret exists, `twitch-service` still runs fine — it just
returns "not configured" and the poll loop stays idle.

## Known limitations (intentional)

- **No `/valorant store` or `/valorant link-store`.** There is no public,
  redirect-based Riot OAuth available to unapproved third-party apps. The
  only real-world way to fetch a personal store rotation is for the bot to
  handle a user's raw Riot username/password — a credential-harvesting
  pattern this build deliberately does not implement.
- **Rank-up detection is tier-level only**, not per-match win/loss.
- **Substitutions get full-match credit** — no partial-credit accounting.
- **Captain-selection/draft state lives in the gateway's memory, not the
  database.** If the gateway restarts mid-draft, that session is lost and
  the 10 players re-queue — no MMR/coins/match rows exist yet at that point.

## Local development

The full stack is designed to run under Kubernetes (see below) — that's the
easiest way to exercise all 7 services together. For working on a single
service in isolation, each `services/<name>/` and `gateway/` directory is a
self-contained Python app with its own `requirements.txt`:

```bash
cd services/db && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DB_PATH=./bot.db PORT=8091 python app.py
```

Point a dependent service at it locally via `DB_SERVICE_URL=http://localhost:8091`, etc. — every service's `config.py` reads its dependency URLs from
env vars with the Kubernetes DNS names as defaults.

## Deployment: local Kubernetes (minikube)

Runs on a **local** Kubernetes cluster (minikube) on this machine —
genuinely free forever, real Kubernetes, no cloud signup. The bot is only
online while this machine and minikube are running — there's no 24/7
hosting here. A Discord bot must never run 2+ gateway pods on the same
token — `k8s/gateway/deployment.yaml` is pinned to `replicas: 1` and
`strategy: Recreate` for exactly this reason.

### 1. One-time toolchain install

```bash
brew install colima docker kubectl minikube
colima start
minikube start --driver=docker
```

### 2. Build every image directly into minikube (no registry needed)

```bash
eval $(minikube docker-env)
for svc in db blackjack coinflip slots economy valorant inhouse twitch; do
  docker build -t "$svc-service:latest" "services/$svc"
done
docker build -t gateway:latest gateway
```
Every `k8s/*/deployment.yaml` references these images with
`imagePullPolicy: Never`, since they only exist in minikube's own Docker
daemon (no registry/push/login needed).

### 3. Create namespaces, secrets, and apply everything

```bash
kubectl apply -f k8s/namespaces.yaml

kubectl create secret generic gateway-secrets -n gateway \
  --from-literal=DISCORD_BOT_TOKEN=xxx --from-literal=GUILD_ID=xxx
kubectl create secret generic valorant-secrets -n valorant \
  --from-literal=HENRIKDEV_API_KEY=xxx
# Optional -- twitch-service runs fine without this, just stays "not configured":
kubectl create secret generic twitch-secrets -n twitch \
  --from-literal=TWITCH_CLIENT_ID=xxx --from-literal=TWITCH_CLIENT_SECRET=xxx

kubectl apply -f k8s/data/pvc.yaml -f k8s/data/deployment.yaml -f k8s/data/service.yaml
kubectl apply -f k8s/casino/blackjack-deployment.yaml -f k8s/casino/blackjack-service.yaml
kubectl apply -f k8s/casino/coinflip-deployment.yaml -f k8s/casino/coinflip-service.yaml
kubectl apply -f k8s/casino/slots-deployment.yaml -f k8s/casino/slots-service.yaml
kubectl apply -f k8s/economy/deployment.yaml -f k8s/economy/service.yaml
kubectl apply -f k8s/valorant/deployment.yaml -f k8s/valorant/service.yaml
kubectl apply -f k8s/inhouse/deployment.yaml -f k8s/inhouse/service.yaml
kubectl apply -f k8s/twitch/deployment.yaml -f k8s/twitch/service.yaml
kubectl apply -f k8s/gateway/deployment.yaml

kubectl get pods -A                       # confirm everything's Running
kubectl logs -f deployment/gateway -n gateway
```

Bring `db-service` up *before* the others so the PVC is bound and the
schema is initialized; the gateway should go up last, once every backend
service it depends on is already reachable. Each namespace's
`secret.example.yaml` documents the literals but is never applied
directly — reference only, placeholder values.

### 4. Shipping a code update

Rebuild that service's image and restart just that Deployment:
```bash
eval $(minikube docker-env)
docker build -t blackjack-service:latest services/blackjack
kubectl rollout restart deployment/blackjack-service -n casino
```

### 5. Backups

The SQLite file lives on a hostPath-backed volume inside minikube's own VM
disk, mounted only into `db-service`'s pod. To pull a real backup copy:
```bash
kubectl cp data/$(kubectl get pod -n data -l app=db-service -o jsonpath='{.items[0].metadata.name}'):/app/data/bot.db ./bot.db.bak
```

### Stopping / tearing down

```bash
kubectl delete -f k8s/gateway/deployment.yaml   # stop just the bot (backend services keep running)
minikube stop                                    # pause the whole cluster (fast to resume)
minikube delete                                  # fully remove the cluster
```

## Architecture notes

- All DB writes funnel through `services/db/database/db.py`'s single
  `aiosqlite` connection guarded by one `asyncio.Lock`, so concurrent
  requests never hit "database is locked." Any read-then-write operation
  (e.g. "insert into the queue, then check if it's full") is wrapped in a
  single `async with db.transaction()` block in `db-service` rather than
  separate calls, to avoid interleaving with another coroutine's write.
- `db-service`'s `/economy/adjust` endpoint is the one choke point every
  other service uses to touch coin balances — it always writes a matching
  `economy_transactions` row, so balance history stays a complete audit trail.
- The inhouse voting/override flow (in `inhouse-service`) treats a
  correction as "revert, then reapply" rather than just flipping a flag: it
  undoes the exact MMR delta and coin reward that were previously applied
  (and unsettles any predictions) before reapplying with the corrected winner.
- The gateway's `utils/clients.py` holds one shared `ServiceClient` per
  backend service (started/closed once in `bot.py`'s `setup_hook`/`close`),
  so every cog reuses the same aiohttp connection pool instead of opening
  new ones per request.
