# Discord Inhouse/Economy Bot

A single-guild Discord bot with six systems: coin economy ("Pink Slips" by
default), casino games (Blackjack/Coinflip/Slots), a Valorant rank/match
tracker (with per-account and per-match grades + stat-derived tips), a full
inhouse custom-game system (queue, draft, map vote, MMR, predictions,
NeatQueue-style result voting), Twitch live announcements, and a FredBoat-style
music player. `/help` lists every command, grouped by system.

## Architecture: gateway + backend microservices

This runs as **15 pods across 7 Kubernetes namespaces**, not one process:

```
Discord  <-->  gateway (ns: gateway)
                  |  the ONLY thing that imports discord.py / holds the token
                  |  cogs/*.py are thin HTTP clients -- no business logic, no DB access
                  |  also runs the music player (voice must live in this process)
                  v
   ns: casino (5 pods)         economy-service   valorant-service   inhouse-service   twitch-service
   blackjack-service (x1)      (ns: economy)     (ns: valorant)     (ns: inhouse)     (ns: twitch)
   coinflip-service  (x2)      balance/daily/    HenrikDev calls,   queue/draft/match/  Twitch Helix calls,
   slots-service     (x2)      weekly/give       rank+match grades,  map vote/voting/    live-poll check
   (each its own pod --                          tips, rank-index    elo/predict
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
   and renders the result itself. Voice playback (the music cog) has the same
   constraint one level deeper: only the process holding the gateway
   connection can open a voice UDP socket, so music stays in the gateway too
   rather than becoming its own service.
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

## Cluster topology: 2 nodes, node affinity

minikube runs as **2 nodes** (`minikube` + `minikube-m02`), labeled by role:

```bash
kubectl label node minikube tier=core
kubectl label node minikube-m02 tier=games
```

- **Casino pods** (`blackjack-service`, `coinflip-service`, `slots-service`)
  have a **hard** `nodeAffinity` requiring `tier=games` — they always land on
  `minikube-m02`.
- **Everything else** (`db-service`, `economy-service`, `valorant-service`,
  `inhouse-service`, `twitch-service`, `gateway`) has a **soft**
  `nodeAffinity` preferring `tier=core` — they land on `minikube` when it has
  room, but aren't blocked from scheduling elsewhere if it doesn't.

Every pod also has liveness/readiness probes hitting a `/healthz` route (the
gateway, which has no HTTP server otherwise, runs a small embedded aiohttp
server just for this — `/healthz/live` is always up once the process is
running, `/healthz/ready` only returns 200 once it's actually connected to
Discord).

## GitOps: ArgoCD

Deployment changes flow through **git → ArgoCD**, not direct `kubectl apply`
to running Deployments. ArgoCD watches this repo's `k8s/` directory
(`argocd/discord-bot-app.yaml`) and auto-syncs + self-heals — editing a live
Deployment by hand gets reverted back to whatever's in git within a few
minutes (or instantly with a forced refresh, see below).

**Installing ArgoCD** (one-time, part of cluster bring-up):
```bash
kubectl create namespace argocd
kubectl apply -n argocd --server-side --force-conflicts \
  -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available --timeout=180s \
  deployment/argocd-server deployment/argocd-repo-server deployment/argocd-dex-server \
  deployment/argocd-applicationset-controller deployment/argocd-notifications-controller \
  deployment/argocd-redis -n argocd
```
(`--server-side` avoids a real gotcha: plain client-side `apply` on this
manifest fails with `metadata.annotations: Too long` — the full manifest
exceeds the `kubectl.kubernetes.io/last-applied-configuration` annotation's
size limit.)

Then point it at this repo:
```bash
kubectl apply -f argocd/discord-bot-app.yaml
```
If you fork this repo, update `spec.source.repoURL` in that file first —
it's hardcoded to the origin repo. Note `spec.source.directory.exclude:
"**/secret.example.yaml"` — this is deliberate: those files are placeholders
with literal `"xxx"` values, and ArgoCD must never apply them over the real
secrets created imperatively (below).

**Checking status / forcing a sync:**
```bash
kubectl get application discord-bot -n argocd    # SYNC STATUS / HEALTH STATUS
kubectl patch application discord-bot -n argocd --type merge \
  -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
```
**ArgoCD UI** (optional): `kubectl port-forward svc/argocd-server -n argocd 8080:443`,
browse `https://localhost:8080`, username `admin`, password from
`kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d`.

### Shipping a code change (the actual day-to-day loop)

```bash
# 1. Build a NEW versioned tag -- never reuse an old tag for an update.
docker build -t valorant-service:v6 services/valorant

# 2. Load into BOTH minikube nodes.
minikube image load valorant-service:v6

# 3. Bump the tag in the manifest, then commit + push.
#    (edit k8s/valorant/deployment.yaml's image: line)
git add -A && git commit -m "..." && git push

# 4. ArgoCD picks it up on its own poll (~3 min) -- or force it:
kubectl patch application discord-bot -n argocd --type merge \
  -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
kubectl rollout status deployment/valorant-service -n valorant
```

**Why step 1 says "never reuse an old tag":** `minikube image load` can
silently fail to replace an image if a running pod already holds the old
content under that same tag (`docker rmi` refuses while a container is using
it) — the load command reports success, but the node keeps the stale image,
and your new code never actually runs. This bit us for real during
development. Always bump to a fresh tag (`v5` → `v6`, not re-pushing `v5`);
it makes the problem structurally impossible instead of relying on
remembering to check.

**Verify you're not still on the stale image**, if in doubt:
```bash
docker inspect --format='{{.Id}}' valorant-service:v6
kubectl exec -n valorant deploy/valorant-service -- true 2>/dev/null; \
kubectl get pods -n valorant -o jsonpath='{.items[*].spec.containers[0].image}'
```

## Configuring the bot: `/settings`

Most per-server tunables are configured **in Discord**, not by editing
files — run `/settings show` (staff/admin only) to see current values, and:

- `/settings currency name: emoji:` — rename the coin currency.
- `/settings valorant-channel channel:` — where rank-up announcements post.
- `/settings staff-role role:` — the staff/mod role: allowed to use
  `/inhouse override`/`cancel`/`debug-fill`, pinged on disputed inhouse
  matches, and allowed to use the Twitch moderation commands below.
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

## Valorant tracker: rank, matches, grades, tips

`/valorant link riot_name: riot_tag: region:` links a Riot ID. `/valorant
rank` shows current tier/RR plus performance stats from the last 10
round-based matches (K/D, avg ACS, avg ADR, headshot %) and an **overall
grade (S–F)** — a weighted composite of rank + ACS + K/D, *not* an official
Riot/HenrikDev stat (weights/thresholds are tunable constants in
`services/valorant/config.py`). `/valorant matches` shows the same
per-match stats plus a per-match grade and up to 2 stat-derived tips (e.g.
high first-death rate, low headshot %, low ADR) computed from HenrikDev's
raw kill-event data. Deathmatch games are excluded from ACS/ADR/grading —
HenrikDev reports `rounds_played=1` for non-round modes, which would
otherwise produce nonsense numbers.

## Inhouse: queue, draft, map vote, results

`/inhouse join`/`leave`/`status` manage the 10-player queue. Once full,
captains are chosen (volunteer, highest-MMR, or random) and teams are
drafted (snake, random, or balanced-by-MMR). Right before the match is
created, everyone votes on a map from the current competitive pool
(`config.INHOUSE_MAP_POOL` in `gateway/config.py` — update this by hand when
Riot rotates the pool); plurality wins, ties and no-votes fall back to
random. `/inhouse finish` starts NeatQueue-style majority-vote result
resolution, which updates MMR/coins and settles any `/inhouse predict` bets.

**Stuck on "Volunteer Captains"?** It needs 2 distinct real clicks with no
built-in escape — staff can hit **"Force Remaining (Staff)"** on that view to
auto-fill whichever captain slot(s) are still open.

**Testing solo:** `/inhouse debug-fill` (staff-only) fills the rest of the
queue with 9 fake players (IDs `"1"`–`"9"`, which can never collide with a
real Discord snowflake) and auto-resolves captains/draft method so you can
walk the entire join → draft → map vote → match → finish-vote → MMR/economy
flow by yourself. Fake players auto-vote on the map and auto-vote the
result to match whatever you pick, so nothing ever hangs waiting on them.

## Music

`/music play <query or URL>`, `skip`, `pause`, `resume`, `stop`, `leave`,
`queue`, `nowplaying`. Resolves via `yt-dlp` and streams straight into the
voice channel via `ffmpeg` (baked into the gateway's image — no extra host
setup needed) — nothing is downloaded or saved, it's ephemeral playback only,
same as any mainstream Discord music bot. Auto-disconnects after 5 minutes
idle or alone in the channel.

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
- **Grades and tips (Valorant + inhouse) are our own heuristics**, not
  official stats — documented as such in-app (footer notes) and in
  `services/valorant/config.py`'s comments.
- **Rank-up detection is tier-level only**, not per-match win/loss.
- **Substitutions get full-match credit** — no partial-credit accounting.
- **Captain-selection/draft state lives in the gateway's memory, not the
  database.** If the gateway restarts mid-draft, that session is lost and
  the 10 players re-queue — no MMR/coins/match rows exist yet at that point.
- **Single-guild only.** Command sync and the rankup/live-poll loops
  currently target one hardcoded `GUILD_ID` — inviting the bot to a second
  server won't register commands there or isolate that server's data.

## Local development

The full stack is designed to run under Kubernetes (see below) — that's the
easiest way to exercise all 8 services together. For working on a single
service in isolation, each `services/<name>/` and `gateway/` directory is a
self-contained Python app with its own `requirements.txt`:

```bash
cd services/db && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DB_PATH=./bot.db PORT=8091 python app.py
```

Point a dependent service at it locally via `DB_SERVICE_URL=http://localhost:8091`, etc. — every service's `config.py` reads its dependency URLs from
env vars with the Kubernetes DNS names as defaults.

## Deployment: local Kubernetes (minikube), from scratch

Runs on a **local, 2-node** Kubernetes cluster (minikube) on this machine —
genuinely free forever, real Kubernetes, no cloud signup. The bot is only
online while this machine and minikube are running — there's no 24/7
hosting here. A Discord bot must never run 2+ gateway pods on the same
token — `k8s/gateway/deployment.yaml` is pinned to `replicas: 1` and
`strategy: Recreate` for exactly this reason.

### 1. One-time toolchain install

```bash
brew install colima docker kubectl minikube
colima start --cpu 4 --memory 4
minikube start --driver=docker --nodes=2 --cpus=2 --memory=1900
```

### 2. Label the nodes for casino affinity

```bash
kubectl label node minikube tier=core
kubectl label node minikube-m02 tier=games
```

### 3. Build every image directly into minikube (no registry needed)

```bash
for svc in db blackjack coinflip slots economy valorant inhouse twitch; do
  docker build -t "$svc-service:v1" "services/$svc"
  minikube image load "$svc-service:v1"
done
docker build -t gateway:v1 gateway
minikube image load gateway:v1
```
Every `k8s/*/deployment.yaml` references these images with
`imagePullPolicy: Never`, since they only exist in minikube's own Docker
daemon (no registry/push/login needed) — see the versioned-tag note above
for why `minikube image load` needs an actual new tag each time, even on
this very first load.

### 4. Create namespaces and secrets

```bash
kubectl apply -f k8s/namespaces.yaml

kubectl create secret generic gateway-secrets -n gateway \
  --from-literal=DISCORD_BOT_TOKEN=xxx --from-literal=GUILD_ID=xxx
kubectl create secret generic valorant-secrets -n valorant \
  --from-literal=HENRIKDEV_API_KEY=xxx
# Optional -- twitch-service runs fine without this, just stays "not configured":
kubectl create secret generic twitch-secrets -n twitch \
  --from-literal=TWITCH_CLIENT_ID=xxx --from-literal=TWITCH_CLIENT_SECRET=xxx
```
Each namespace's `secret.example.yaml` documents the literals but is never
applied directly — reference only, placeholder values.

### 5. Install ArgoCD and point it at this repo

Follow the **GitOps: ArgoCD** section above (`kubectl create namespace argocd`
→ apply the install manifest → `kubectl apply -f argocd/discord-bot-app.yaml`,
after updating `repoURL` if you forked this repo).

```bash
kubectl get application discord-bot -n argocd   # wait for Synced / Healthy
kubectl get pods -A                              # confirm everything's Running
kubectl logs -f deployment/gateway -n gateway
```
ArgoCD applies `k8s/` in dependency-agnostic order — `db-service` binds its
PVC and initializes the schema on its own startup regardless of what order
pods come up in, so there's no manual sequencing to get right here (unlike
a bare `kubectl apply` walkthrough).

### 6. Backups

The SQLite file lives on a hostPath-backed volume inside minikube's own VM
disk, mounted only into `db-service`'s pod. To pull a real backup copy:
```bash
kubectl cp data/$(kubectl get pod -n data -l app=db-service -o jsonpath='{.items[0].metadata.name}'):/app/data/bot.db ./bot.db.bak
```

### Restarting after this Mac reboots (or Claude/your terminal closes)

Cluster state (deployments, PVC data, ArgoCD) persists on disk as long as you
only ever `stop` (never `delete`) colima/minikube. The 2-node topology is
saved in minikube's profile config, so a plain `minikube start` brings both
nodes back:
```bash
colima start
minikube start
```
Everything else (bot, all 8 backend services, db data, ArgoCD) resumes on
its own — no rebuilding or reapplying manifests needed.

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
- Schema upgrades to an *existing* table use `services/db/database/init_db.py`'s
  `COLUMN_MIGRATIONS` list (plain, idempotent `ALTER TABLE ... ADD COLUMN`) —
  `CREATE TABLE IF NOT EXISTS` in `schema.sql` only helps brand-new tables,
  it's a no-op against a table that already exists with an older, narrower
  column set.
