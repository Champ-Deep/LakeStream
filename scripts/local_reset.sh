#!/usr/bin/env bash
#
# Tear down any previous LakeStream stack, then bring up a clean one and smoke
# test it.
#
#   ./scripts/local_reset.sh              # project-scoped reset (safe default)
#   ./scripts/local_reset.sh --prune      # also reclaim dangling images + build cache
#   ./scripts/local_reset.sh --prune-all  # also remove ALL unused images/volumes (asks first)
#   ./scripts/local_reset.sh --down-only  # tear down and stop
#
# The default touches only this project's containers, networks and volumes.
# Anything that can affect other projects on the machine is opt-in and, for
# --prune-all, requires typing "yes".

set -euo pipefail

cd "$(dirname "$0")/.."

COMPOSE_FILE="docker-compose.local.yml"
API_URL="http://localhost:7100"
PRUNE="none"
DOWN_ONLY=0

for arg in "$@"; do
  case "$arg" in
    --prune)     PRUNE="safe" ;;
    --prune-all) PRUNE="all" ;;
    --down-only) DOWN_ONLY=1 ;;
    -h|--help)   sed -n '3,13p' "$0" | sed 's/^# \?//'; exit 0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if ! docker info >/dev/null 2>&1; then
  echo "Docker does not appear to be running. Start Docker Desktop and retry." >&2
  exit 1
fi

# `docker compose` (v2) vs legacy `docker-compose`.
if docker compose version >/dev/null 2>&1; then
  DC=(docker compose)
else
  DC=(docker-compose)
fi

say() { printf '\n\033[1m== %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
say "What is running for this project right now"
# Both compose files are declared so `down` also catches a stack started with
# the other one. No explicit project name is set in either file, so Compose
# derives it from this directory.
"${DC[@]}" -f "$COMPOSE_FILE" ps 2>/dev/null || true
"${DC[@]}" -f docker-compose.yml ps 2>/dev/null || true

echo
echo "Any other containers built from this repo's images (not managed by the"
echo "compose files above) would show here:"
docker ps -a --filter "name=lakestream" --format '  {{.Names}}\t{{.Image}}\t{{.Status}}' || true

# ---------------------------------------------------------------------------
say "Tearing down this project's stack (containers + networks + its volumes)"
# -v drops the named volumes (pgdata_local, screenshots_local) so the next boot
# gets a fresh database and re-runs every migration from scratch.
# --remove-orphans clears services that were removed from the compose file.
"${DC[@]}" -f "$COMPOSE_FILE"   down -v --remove-orphans 2>/dev/null || true
"${DC[@]}" -f docker-compose.yml down -v --remove-orphans 2>/dev/null || true
echo "done."

# ---------------------------------------------------------------------------
case "$PRUNE" in
  safe)
    say "Reclaiming dangling images and build cache (does not touch volumes)"
    docker image prune -f
    docker builder prune -f
    ;;
  all)
    say "Removing ALL unused Docker resources on this machine"
    cat <<'WARN'
This goes beyond LakeStream. It will delete, for every project on this machine:
  - all stopped containers
  - all images not used by a running container
  - all unused volumes  <-- this is the one that loses data
  - all unused networks and the build cache

Any database volume belonging to a project that is merely stopped right now
counts as "unused" and will be deleted.
WARN
    read -r -p 'Type "yes" to proceed: ' reply
    if [ "$reply" = "yes" ]; then
      docker system prune -a --volumes -f
    else
      echo "Skipped. Nothing outside this project was removed."
    fi
    ;;
esac

if [ "$DOWN_ONLY" -eq 1 ]; then
  say "Teardown complete (--down-only)"
  exit 0
fi

# ---------------------------------------------------------------------------
say "Building and starting the stack"
"${DC[@]}" -f "$COMPOSE_FILE" up --build -d

# ---------------------------------------------------------------------------
say "Waiting for the API to answer (migrations run on its startup)"
for i in $(seq 1 90); do
  if curl -fsS -o /dev/null "$API_URL/ping" 2>/dev/null; then
    echo "API up after ${i}s."
    break
  fi
  if [ "$i" -eq 90 ]; then
    echo "API did not come up within 90s. Recent logs:" >&2
    "${DC[@]}" -f "$COMPOSE_FILE" logs --tail=40 api >&2
    exit 1
  fi
  sleep 1
done

# ---------------------------------------------------------------------------
say "Smoke test"
printf '  migrations applied: '
"${DC[@]}" -f "$COMPOSE_FILE" exec -T postgres \
  psql -U scraper -d lakeb2b_scraper -tAc 'select count(*) from _migrations' 2>/dev/null \
  | tr -d '[:space:]' || echo '?'
echo

printf '  duplicate migration numbers: '
dupes=$(ls src/db/migrations/ | sed -E 's/^([0-9]+).*/\1/' | sort | uniq -d | tr '\n' ' ')
if [ -n "${dupes// /}" ]; then echo "FOUND: $dupes"; else echo "none"; fi

printf '  Go HTTP sidecar: '
"${DC[@]}" -f "$COMPOSE_FILE" exec -T go-http-fetcher \
  wget -qO- --post-data '{"url":"https://example.com","options":{"timeout_ms":15000}}' \
  --header 'Content-Type: application/json' http://localhost:8080/fetch 2>/dev/null \
  | head -c 60 || echo 'no response (check: docker compose logs go-http-fetcher)'
echo

printf '  admin login: '
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$API_URL/login" \
  -d 'email=admin@lakeb2b.internal' --data-urlencode 'password=LakeB2B_admin!')
[ "$code" = "302" ] && echo "OK (302 redirect)" || echo "unexpected HTTP $code"

cat <<EOF

== Ready ==
  URL       $API_URL
  login     admin@lakeb2b.internal  /  LakeB2B_admin!
  logs      ${DC[*]} -f $COMPOSE_FILE logs -f api worker
  stop      ${DC[*]} -f $COMPOSE_FILE down
  reset     ./scripts/local_reset.sh

Try the merged tech-stack detection at $API_URL/tech — paste a domain
(wordpress.org, vercel.com, stripe.com) and check the confidence column.
EOF
