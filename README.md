# Discord Inhouse/Economy Bot

A Discord bot with six systems: a coin economy ("Pink Slips" by default),
casino games (Blackjack, Coinflip, Slots), a Valorant rank/match tracker
with per-account and per-match grades and stat-derived tips, a full inhouse
custom-game system (queue, draft, map vote, MMR, predictions, NeatQueue-style
result voting), Twitch live announcements, and a music player. `/help` lists
every command, grouped by system.

## Architecture

The bot runs as 15 pods across 7 Kubernetes namespaces:

```
Discord  <-->  gateway (ns: gateway)
                  |  the only process that imports discord.py / holds the token
                  |  cogs/*.py are thin HTTP clients: no business logic, no DB access
                  |  also runs the music player, since voice must live in this process
                  v
   ns: casino (5 pods)         economy-service   valorant-service   inhouse-service   twitch-service
   blackjack-service (x1)      (ns: economy)     (ns: valorant)     (ns: inhouse)     (ns: twitch)
   coinflip-service  (x2)      balance/daily/    HenrikDev calls,   queue/draft/match/  Twitch Helix calls,
   slots-service     (x2)      weekly/give       rank+match grades,  map vote/voting/    live-poll check
   (each its own pod:                            tips, rank-index    elo/predict
   one game per process)
                  \_____________|______________|______________|____________/
                                        |
                                  db-service (ns: data)
                              owns bot.db and the PVC, the
                              only service touching SQLite directly
```

Casino is split one level finer than the other domains: blackjack, coinflip,
and slots each run as their own pod (`blackjack-service`, `coinflip-service`,
`slots-service`, all in the `casino` namespace) rather than one combined
`casino-service`. This split isn't driven by independent scaling or failure
requirements: the three games change together and share the same
data-access pattern. It's structured this way to exercise more of the
Kubernetes surface (separate Services, Deployments, and DNS names per game).

Two constraints shape the overall design:

1. **Only one process can hold the Discord gateway connection for a given
   token.** Business-logic services carry no Discord token at all; the
   gateway calls them over plain HTTP/JSON and renders the result. Voice
   playback has the same constraint one level deeper (only the process
   holding the gateway connection can open a voice UDP socket), so the
   music player also lives in the gateway rather than as its own service.
2. **SQLite can't be written by multiple processes over the network**, and
   the Kubernetes PVC here is `ReadWriteOnce` (it can't be mounted by two
   pods at once). `db-service` is the sole owner of `bot.db`; every other
   service reaches it over the cluster network instead of importing a
   database module directly.

Discord-specific side effects that only the gateway can perform (creating
inhouse match channels, moving members between voice channels, posting
announcement embeds) stay in the gateway's cogs, even when the underlying
decision (who won, whether a stream just started) is made by a backend
service. The gateway always initiates calls to services, never the reverse,
so no service needs to expose anything back to Discord itself.

Internal calls are plain `aiohttp.web` JSON over ClusterIP Services
(`http://<service>.<namespace>.svc.cluster.local`), with no auth between
them. That's a reasonable simplification for internal-only services on a
personal cluster with no internet exposure.

## Cluster topology

minikube runs as a single node, labeled by role (this repo used to run 2
nodes; it was collapsed to 1 to free up memory on an 8GB host, see
"Resource constraints" below):

```bash
kubectl label node minikube tier=games
```

- **Casino pods** (`blackjack-service`, `coinflip-service`, `slots-service`)
  have a hard `nodeAffinity` requiring `tier=games`.
- **Everything else** (`db-service`, `economy-service`, `valorant-service`,
  `inhouse-service`, `twitch-service`, `gateway`) has a soft `nodeAffinity`
  preferring `tier=core`. With only one node in the cluster, this preference
  is currently a no-op; the affinity rules are left in place since they cost
  nothing and are what you'd relabel around if a second node is ever added
  back.

### Resource constraints

This runs on an 8GB Apple Silicon Mac via colima, which reserves a single
fixed-size VM shared by every node in the cluster regardless of node count.
That VM is currently sized to 3GB (`colima start --memory 3`), leaving the
rest of the Mac's RAM for normal desktop use. Running a second node roughly
doubles per-node overhead (its own kubelet, kube-proxy, container runtime)
for no real gain on a single physical machine, so this repo runs one node.
If you have more RAM to spare, `minikube start --nodes=2` plus the
`tier=core`/`tier=games` labels above still works exactly as documented.

Every pod has liveness/readiness probes hitting a `/healthz` route. The
gateway has no HTTP server otherwise, so it runs a small embedded aiohttp
server just for this: `/healthz/live` is up once the process is running,
`/healthz/ready` only returns 200 once it's actually connected to Discord.

## GitOps: ArgoCD

Deployment changes flow through git and ArgoCD, not direct `kubectl apply`
to running Deployments. ArgoCD watches this repo's `k8s/` directory
(`argocd/discord-bot-app.yaml`) and auto-syncs with self-heal enabled:
editing a live Deployment by hand gets reverted back to whatever's in git
within a few minutes, or instantly with a forced refresh (below).

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
`--server-side` is required here: plain client-side `apply` on this manifest
fails with `metadata.annotations: Too long`, since the full manifest exceeds
the size limit of the `kubectl.kubernetes.io/last-applied-configuration`
annotation.

Then point it at this repo:
```bash
kubectl apply -f argocd/discord-bot-app.yaml
```
If you fork this repo, update `spec.source.repoURL` in that file first; it's
set to the origin repo by default. Note `spec.source.directory.exclude:
"**/secret.example.yaml"`: those files are placeholders with literal
`"xxx"` values, and ArgoCD must never apply them over the real secrets
created imperatively (below).

**Checking status or forcing a sync:**
```bash
kubectl get application discord-bot -n argocd    # sync status / health status
kubectl patch application discord-bot -n argocd --type merge \
  -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
```
**ArgoCD UI** (optional): `kubectl port-forward svc/argocd-server -n argocd 8080:443`,
browse `https://localhost:8080`, username `admin`, password from
`kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d`.

### Shipping a code change

```bash
# 1. Build a new versioned tag. Don't reuse an old tag for an update.
docker build -t valorant-service:v6 services/valorant

# 2. Load into both minikube nodes.
minikube image load valorant-service:v6

# 3. Bump the tag in the manifest, then commit and push.
#    (edit k8s/valorant/deployment.yaml's image: line)
git add -A && git commit -m "..." && git push

# 4. ArgoCD picks it up on its own poll interval (about 3 minutes), or force it:
kubectl patch application discord-bot -n argocd --type merge \
  -p '{"metadata":{"annotations":{"argocd.argoproj.io/refresh":"hard"}}}'
kubectl rollout status deployment/valorant-service -n valorant
```

Step 1 matters: `minikube image load` can silently fail to replace an image
if a running pod already holds the old content under that same tag, since
`docker rmi` refuses to remove an image a container is using. The load
command still reports success, but the node keeps the stale image and the
new code never runs. Bumping to a fresh tag every time (`v5` to `v6`, not
re-pushing `v5`) avoids this entirely.

**To confirm a pod is running the image you think it is:**
```bash
docker inspect --format='{{.Id}}' valorant-service:v6
kubectl get pods -n valorant -o jsonpath='{.items[*].spec.containers[0].image}'
```

## Monitoring: Prometheus

`k8s/monitoring/` runs a single Prometheus pod (no Grafana, no
Alertmanager, no node-exporter) scraping per-container CPU/memory straight
from each node's kubelet (cAdvisor), proxied through the API server. That
needs no `/metrics` endpoint added to any of the bot's own services, just
the RBAC in `k8s/monitoring/rbac.yaml` letting Prometheus's service account
read node/pod info. Deliberately kept minimal given the 8GB host: 6-hour
retention on `emptyDir` storage (no PVC, fine to lose on pod restart), a
60s scrape interval, and tight `resources.limits` (256Mi/200m).

Access the UI via port-forward (no Ingress is set up for this):
```bash
kubectl port-forward svc/prometheus -n monitoring 9090:9090
```
then browse `http://localhost:9090`. `/targets` shows scrape health;
`/graph` runs PromQL, for example
`container_memory_working_set_bytes{namespace="casino"}` for per-pod memory
across the casino services.

## Configuring the bot: `/settings`

Most per-server settings are configured in Discord, not by editing files.
Run `/settings show` (staff/admin only) to see current values:

- `/settings currency name: emoji:`: rename the coin currency.
- `/settings valorant-channel channel:`: where rank-up announcements post.
- `/settings staff-role role:`: the staff/mod role, allowed to use
  `/inhouse override`/`cancel`/`debug-fill`, pinged on disputed inhouse
  matches, and allowed to use the Twitch moderation commands below.
- `/settings voice-category category:`: where temporary inhouse match
  voice/text channels are created.
- `/settings twitch-channel channel:`: where Twitch live announcements post.

These settings live in `db-service`'s `guild_settings` table, not `.env`, so
changing them doesn't require a restart. Secrets (`DISCORD_BOT_TOKEN`,
`HENRIKDEV_API_KEY`, `TWITCH_CLIENT_ID`/`SECRET`) and boot-time config
(`GUILD_ID`, `DB_PATH`) stay env-only. The corresponding
`VALORANT_UPDATES_CHANNEL_ID`/`INHOUSE_STAFF_ROLE_ID`/
`INHOUSE_VOICE_CATEGORY_ID` env vars only matter as first-run seed values:
`db-service` copies them into `guild_settings` the first time it sees a
guild, and `/settings` takes over from there.

## Valorant tracker

`/valorant link riot_name: riot_tag: region:` links a Riot ID. `/valorant
rank` shows current tier/RR plus performance stats from the last 10
Competitive matches (K/D, avg ACS, avg ADR, headshot %) and an overall grade
(S through F): a weighted composite of rank, ACS, and K/D. This grade is not
an official Riot/HenrikDev stat; weights and thresholds are tunable
constants in `services/valorant/config.py`. `/valorant matches` shows the
same per-match stats plus a per-match grade and up to 2 stat-derived tips
(for example, high first-death rate, low headshot %, low ADR), computed
from HenrikDev's raw kill-event data. Deathmatch games are excluded from
ACS/ADR/grading, since HenrikDev reports `rounds_played=1` for non-round
modes, which would otherwise produce meaningless numbers.

## Inhouse system

`/inhouse join`/`leave`/`status` manage the 10-player queue. Once full,
captains are chosen (volunteer, highest-MMR, or random) and teams are
drafted (snake, random, or balanced-by-MMR). Before the match is created,
everyone votes on a map from the current competitive pool
(`config.INHOUSE_MAP_POOL` in `gateway/config.py`; update this list by hand
when Riot rotates the map pool). Plurality wins; ties and no-votes fall back
to a random pick. `/inhouse finish` starts NeatQueue-style majority-vote
result resolution, which updates MMR/coins and settles any `/inhouse
predict` bets.

If "Volunteer Captains" is selected but stalls (it needs 2 distinct
clicks), staff can use the **"Force Remaining (Staff)"** button on that view
to auto-fill whichever captain slot(s) are still open.

`/inhouse debug-fill` (staff-only) fills the rest of the queue with 9 fake
players (IDs `"1"` through `"9"`, which can never collide with a real
Discord snowflake) and auto-resolves captains and draft method, so the
entire join, draft, map vote, match, finish-vote, and MMR/economy flow can
be tested solo. Fake players auto-vote on the map and auto-vote the result
to match whatever the real player picks.

## Music

`/music play <query or URL>`, `skip`, `pause`, `resume`, `stop`, `leave`,
`queue`, `nowplaying`. Tracks are resolved via `yt-dlp` and streamed
directly into the voice channel via `ffmpeg` (bundled into the gateway's
image). Nothing is downloaded or saved; playback is ephemeral, the same as
any standard Discord music bot. The player auto-disconnects after 5 minutes
idle or alone in the channel.

## Twitch live announcements

Users self-link with `/twitch link username:`, and the bot posts an
announcement when they go live (checked every few minutes). Staff (the role
set via `/settings staff-role`) get `/twitch list`, `/twitch unlink-user
member:`, and `/twitch announce member:` (checks right now and posts
immediately if the member is live).

Setup requires free Twitch credentials:

1. Go to https://dev.twitch.tv/console/apps and register an application.
2. Name it anything; set OAuth Redirect URL to `https://localhost` (required
   by the form but unused, since this only performs an app-level
   client-credentials grant, never a per-user login); category "Chat Bot".
3. Copy the Client ID, then click "New Secret" for the Client Secret.
4. `kubectl create secret generic twitch-secrets -n twitch --from-literal=TWITCH_CLIENT_ID=xxx --from-literal=TWITCH_CLIENT_SECRET=xxx`

Until that secret exists, `twitch-service` still runs; it returns "not
configured" and the poll loop stays idle.

## Casual greeting

Saying "qbert say hi/hello/hey" anywhere gets an instant canned greeting.
(An earlier version routed any other message to a local Ollama model for
open-ended banter; dropped after it repeatedly made the AWS deployment's
`t3.small` instance unresponsive under its memory/CPU footprint -- see
git history around the "chat-service" commits if reviving this.)

## Known limitations

- **No `/valorant store` or `/valorant link-store`.** There is no public,
  redirect-based Riot OAuth available to unapproved third-party apps. The
  only way to fetch a personal store rotation would require the bot to
  handle a user's raw Riot username/password, which this build does not do.
- **Grades and tips (Valorant and inhouse) are custom heuristics**, not
  official stats. This is documented in-app (footer notes) and in
  `services/valorant/config.py`.
- **Rank-up detection is tier-level only**, not per-match win/loss.
- **Substitutions get full-match credit**, with no partial-credit accounting.
- **Captain-selection/draft state lives in the gateway's memory, not the
  database.** If the gateway restarts mid-draft, that session is lost and
  the 10 players have to re-queue; no MMR, coins, or match rows exist yet
  at that point.

## Local development

The full stack is designed to run under Kubernetes (see below), which is
the easiest way to exercise all 8 services together. For working on a
single service in isolation, each `services/<name>/` and `gateway/`
directory is a self-contained Python app with its own `requirements.txt`:

```bash
cd services/db && python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
DB_PATH=./bot.db PORT=8091 python app.py
```

Point a dependent service at it locally via
`DB_SERVICE_URL=http://localhost:8091`, and so on; every service's
`config.py` reads its dependency URLs from env vars, defaulting to the
Kubernetes DNS names.

## Deployment: local Kubernetes (minikube)

The bot runs on a local, single-node Kubernetes cluster (minikube) on this
machine: free, real Kubernetes, no cloud signup required. It's only online
while this machine and minikube are running; there's no 24/7 hosting here.
A Discord bot must never run 2+ gateway pods on the same token, so
`k8s/gateway/deployment.yaml` is pinned to `replicas: 1` and
`strategy: Recreate`.

### 1. One-time toolchain install

```bash
brew install colima docker kubectl minikube
colima start --cpu 4 --memory 3
minikube start --driver=docker --cpus=4 --memory=2830
```

### 2. Label the node for casino affinity

```bash
kubectl label node minikube tier=games
```

### 3. Build every image directly into minikube

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
daemon and no registry is used. See the versioned-tag note above for why
`minikube image load` needs a distinct tag on every load, including this
first one.

### 4. Create namespaces and secrets

```bash
kubectl apply -f k8s/namespaces.yaml

kubectl create secret generic gateway-secrets -n gateway \
  --from-literal=DISCORD_BOT_TOKEN=xxx --from-literal=GUILD_ID=xxx
kubectl create secret generic valorant-secrets -n valorant \
  --from-literal=HENRIKDEV_API_KEY=xxx
# Optional: twitch-service runs fine without this, it just stays "not configured".
kubectl create secret generic twitch-secrets -n twitch \
  --from-literal=TWITCH_CLIENT_ID=xxx --from-literal=TWITCH_CLIENT_SECRET=xxx
```
Each namespace's `secret.example.yaml` documents the literals but is never
applied directly; it's a reference with placeholder values.

### 5. Install ArgoCD and point it at this repo

Follow the ArgoCD section above: create the `argocd` namespace, apply the
install manifest, then `kubectl apply -f argocd/discord-bot-app.yaml`
(update `repoURL` first if you forked this repo).

```bash
kubectl get application discord-bot -n argocd   # wait for Synced / Healthy
kubectl get pods -A                              # confirm everything's Running
kubectl logs -f deployment/gateway -n gateway
```
ArgoCD applies `k8s/` without regard to inter-service ordering. `db-service`
binds its PVC and initializes the schema on its own startup regardless of
when other pods come up, so there's no manual sequencing required here.

### 6. Backups

The SQLite file lives on a hostPath-backed volume inside minikube's own VM
disk, mounted only into `db-service`'s pod. To pull a backup copy:
```bash
kubectl cp data/$(kubectl get pod -n data -l app=db-service -o jsonpath='{.items[0].metadata.name}'):/app/data/bot.db ./bot.db.bak
```

### Restarting after a reboot

Cluster state (deployments, PVC data, ArgoCD) persists on disk as long as
colima/minikube are only ever stopped, not deleted:
```bash
colima start
minikube start
```
Everything else (the bot, all backend services, database, ArgoCD,
Prometheus) resumes on its own; no rebuilding or reapplying manifests is
needed. Note that resizing colima's VM (`colima stop` /
`colima start --memory <n>`) restarts the whole VM, which stops minikube's
node container too; always follow it with `minikube start` to bring the
node back up, then give CoreDNS a few seconds to settle before expecting
inter-service DNS lookups to work.

### Stopping or tearing down

```bash
kubectl delete -f k8s/gateway/deployment.yaml   # stop just the bot; backend services keep running
minikube stop                                    # pause the whole cluster, fast to resume
minikube delete                                  # fully remove the cluster
```

## Deployment: AWS (k3s on EC2)

A second, independent deployment target exists for real cloud/DevOps
practice: [k3s](https://k3s.io) (a lightweight, CNCF-certified Kubernetes
distribution -- same `kubectl`, same manifests) running on a single EC2
instance, using `k8s-aws/` instead of `k8s/`. Kubernetes manifests are
applied by hand (`kubectl apply`), not through ArgoCD -- ArgoCD's own
7-pod footprint made this instance's already-tight cold-start meaningfully
worse (load average over 25 on a 2-vCPU box, versus low-to-mid teens for
the bot stack alone), so it stays a local-only GitOps practice tool
instead.

### Provisioning: Terraform

Everything below the Kubernetes layer -- the EC2 instance, its security
group and key pair, the IAM roles, the scheduler Lambda, and the
EventBridge schedules -- is defined in `terraform/`, not hand-run `aws`
CLI commands. That distinction matters: the first version of this setup
was built by hand, and every time the instance needed replacing (a few
times, this session), it meant re-running a long, easy-to-get-wrong
sequence of CLI commands from memory. Terraform makes that reproducible:

```bash
cd terraform
terraform init      # one-time, or after adding a provider
terraform plan       # review what would change
terraform apply      # create/update everything
terraform destroy    # tear it all down
```

State (`terraform.tfstate`) is local-only, not a remote backend -- fine
for a single-person project, but it means state and the real AWS
resources can only ever be as in-sync as whoever last ran `apply` on this
machine. `terraform.tfstate` and the `.terraform/` provider cache are
gitignored (state can contain secret material, e.g. the private key
resource below); `.terraform.lock.hcl` is committed, since that's what
pins provider versions for reproducible runs.

Adopting Terraform for infrastructure that already existed by hand means
either importing it into state or recreating it. This project recreated:
the old hand-made instance/security group/IAM roles/Lambda/schedules were
deleted first (confirmed no real data on the instance -- SQLite tables
were all empty), then `terraform apply` built fresh equivalents with the
same names. `terraform plan` before applying is what makes this safe to
do with confidence: it shows exactly what will be created before
anything happens.

Notable choices in `terraform/`:
- The SSH key pair is generated by Terraform itself (`tls_private_key` +
  `aws_key_pair`), not `aws ec2 create-key-pair` -- nothing here should
  need a manual AWS console/CLI step ever again.
- The Lambda's zip is built automatically via the `archive_file` data
  source pointing at `aws/ec2-scheduler-lambda/handler.py` -- no manual
  `zip` step.
- The SSM parameter Terraform creates for the current IP
  (`/discord-bot/current-ip`) has `lifecycle { ignore_changes = [value] }`,
  since the Lambda overwrites that value on every scheduled start;
  Terraform manages the parameter's existence, not its live value.
- The instance's ID is passed into the Lambda as an environment variable
  (`INSTANCE_ID`) rather than hardcoded in `handler.py`, since Terraform
  -- not a person -- now decides what that ID is.

**Key differences from the local deployment:**
- Images pull from `ghcr.io/drome05/<service>:<commit-sha>` (built by the
  GitHub Actions CI pipeline) instead of being built and loaded locally.
  Pin the exact commit SHA in each `k8s-aws/*/deployment.yaml`, not
  `:latest` -- same stale-tag lesson as the local setup, just enforced by
  convention here instead of by `minikube image load`'s behavior.
- Single replica everywhere (the local doubled replicas on some services
  are only there for k8s practice, not real redundancy).
- `local-path` storage class (k3s's default) instead of minikube's
  `standard`.
- **Instance architecture must match the images CI built.** GitHub's
  runners are x86 (`linux/amd64`), so the EC2 instance needs to be an x86
  type (`t3.small`, not a Graviton/ARM `t4g.*`) unless CI is changed to
  produce multi-arch images.

### One bot token, two clusters

A Discord bot token can only hold one live gateway connection. Only one of
the two `gateway` Deployments (local `k8s/gateway`, cloud
`k8s-aws/gateway`) should ever be scaled above 0 replicas at a time.

Locally, this has to go through git, not a raw `kubectl scale` --
ArgoCD's self-heal will revert a manual scale back to whatever's
committed within minutes (or instantly with a forced refresh). Flip
`replicas` in `k8s/gateway/deployment.yaml`, commit, push, and force a
sync (see the ArgoCD section above). On the AWS side, since that cluster
isn't ArgoCD-managed, a plain `kubectl scale deployment gateway -n
gateway --replicas=0` on the EC2 box is enough.

### Scheduled stop/start

The EC2 instance stops at 7am and starts at 7pm, America/Chicago time
(matching when the server's actually active), via an EventBridge
Scheduler schedule invoking a small Lambda (`discord-bot-ec2-scheduler`)
that calls `ec2:StopInstances`/`StartInstances`. A stopped instance costs
nothing for compute, only the EBS volume (~$1.60/month for 20GB) --
meaningful savings against a limited promotional credit if the box isn't
needed 24/7. At `t3.small` pricing, this 12h/day window costs roughly
$8-9/month in compute; the account's promotional credit is the binding
constraint on how long this runs, not the schedule itself.

### Instance sizing gotchas (new/promotional AWS accounts)

Two account-level restrictions showed up that aren't documented anywhere
obvious:
- `ec2:ModifyInstanceAttribute` to resize an existing instance's type is
  blocked on new/promotional accounts (`FreeTierRestrictionError`), even
  for a dry-run-approved size. Terminating and launching fresh at the new
  size works instead, if there's no data on the instance worth keeping
  (back it up first if there is -- see the backup command in the local
  deployment section, same idea, different `kubectl` context).
- Directly launching a non-free-tier-eligible instance type
  (`InvalidParameterCombination`) is also blocked. Check what's actually
  allowed with `aws ec2 describe-instance-types --filters
  Name=free-tier-eligible,Values=true`; it includes more than just
  `t3.micro` (notably `c7i-flex.large` and `m7i-flex.large`, which have
  real RAM, but cost enough more per hour that they only make sense with a
  much shorter daily runtime -- run the compute-hours-per-dollar math
  before switching, since a bigger instance only helps if the schedule
  still fits the credit).

`t3.small` has, twice, become fully unresponsive under this workload's
normal operation (not just heavy setup-time load) -- `aws ec2
describe-instance-status` showed `InstanceStatus: impaired` (reachability
failed) while `SystemStatus` stayed `ok`, meaning the underlying hardware
was fine but something inside the guest OS/network stack got stuck. A
stop/start (not a reboot) reliably resets it in under 2 minutes; k3s comes
back on its own via its systemd unit. If this becomes frequent under real
usage, that's a signal this instance size is genuinely undersized for the
full stack's steady-state needs, not something to keep working around.

No Elastic IP is attached, since AWS bills idle Elastic IPs hourly too
(a stopped instance has nothing to attach one to) -- there's no cost
advantage over the ephemeral public IP, which simply disappears while
stopped and gets reassigned on each start. The start half of the Lambda
writes the new IP to SSM Parameter Store instead:
```bash
aws ssm get-parameter --name /discord-bot/current-ip --query 'Parameter.Value' --output text
```
k3s is installed as a systemd service (enabled at boot), so the whole
stack -- k3s itself, every pod, the gateway's Discord connection --
comes back on its own after a scheduled start with no manual
intervention; confirmed by a live stop/start test.

## Architecture notes

- All database writes funnel through `services/db/database/db.py`'s single
  `aiosqlite` connection, guarded by one `asyncio.Lock`, so concurrent
  requests never hit "database is locked." Any read-then-write operation
  (for example, inserting into the queue and then checking if it's full) is
  wrapped in a single `async with db.transaction()` block in `db-service`
  rather than separate calls, to avoid interleaving with another
  coroutine's write.
- `db-service`'s `/economy/adjust` endpoint is the single point every other
  service uses to touch coin balances. It always writes a matching
  `economy_transactions` row, so balance history stays a complete audit trail.
- The inhouse voting/override flow in `inhouse-service` treats a correction
  as "revert, then reapply" rather than flipping a flag: it undoes the exact
  MMR delta and coin reward previously applied (and unsettles any
  predictions) before reapplying with the corrected winner.
- The gateway's `utils/clients.py` holds one shared `ServiceClient` per
  backend service, started and closed once in `bot.py`'s
  `setup_hook`/`close`, so every cog reuses the same aiohttp connection pool
  instead of opening new ones per request.
- Schema upgrades to an existing table use `services/db/database/init_db.py`'s
  `COLUMN_MIGRATIONS` list: a plain, idempotent `ALTER TABLE ... ADD COLUMN`.
  `CREATE TABLE IF NOT EXISTS` in `schema.sql` only helps brand-new tables;
  it's a no-op against a table that already exists with an older, narrower
  column set. Primary-key changes (for example, per-guild data isolation)
  require a full table rebuild instead, handled by one-off scripts such as
  `services/db/migrate_guild_id.py`.
