#!/usr/bin/env bash
#
# dev-link.sh — give LakeStream local development a NAMED public URL instead of
# a bare localhost link, so you can open/share it from any device.
#
# The app must already be running locally (see docker-compose.local.yml, api on
# :7100). This just tunnels that local port out under a stable, named URL.
#
# Usage:
#   ./scripts/dev-link.sh                 # https://lakestream-dev.loca.lt -> :7100
#   ./scripts/dev-link.sh myname 7100     # https://myname.loca.lt        -> :7100
#   TUNNEL_SUBDOMAIN=lenovo-test ./scripts/dev-link.sh
#
# Default backend is localtunnel (npx, no signup, lets you NAME the subdomain).
# On first visit loca.lt shows a one-time interstitial; the "tunnel password" it
# asks for is your machine's public IP (curl https://loca.lt/mytunnelpassword).
# For a zero-interstitial URL (but a random name), use: TUNNEL=cloudflared.
set -euo pipefail

NAME="${1:-${TUNNEL_SUBDOMAIN:-lakestream-dev}}"
PORT="${2:-7100}"
TUNNEL="${TUNNEL:-localtunnel}"

echo "Local:  http://localhost:${PORT}"

case "$TUNNEL" in
  localtunnel|lt)
    echo "Public: https://${NAME}.loca.lt   (share/test this)"
    echo "        first visit password = $(curl -fsS https://loca.lt/mytunnelpassword 2>/dev/null || echo 'curl https://loca.lt/mytunnelpassword')"
    echo "Starting localtunnel… (Ctrl-C to stop)"
    exec npx --yes localtunnel --port "${PORT}" --subdomain "${NAME}"
    ;;
  cloudflared)
    echo "Public: a random https://*.trycloudflare.com URL will print below"
    echo "Starting cloudflared quick tunnel… (Ctrl-C to stop)"
    exec cloudflared tunnel --url "http://localhost:${PORT}"
    ;;
  ngrok)
    echo "Starting ngrok (needs an ngrok account/authtoken for a named domain)…"
    exec ngrok http "${PORT}"
    ;;
  *)
    echo "Unknown TUNNEL='$TUNNEL' (use localtunnel | cloudflared | ngrok)" >&2
    exit 1
    ;;
esac
