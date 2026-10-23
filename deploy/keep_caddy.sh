#!/usr/bin/env bash
# Run every few minutes from cron (setup.sh installs /etc/cron.d/law_help).
# A redeploy of the project that owns Caddy can reset its Caddyfile or recreate its network;
# this puts the law_help site block back and reconnects the app, and does nothing otherwise.
# Usage: keep_caddy.sh law.rigcheck.in [caddy-container]
set -uo pipefail
DOMAIN=${1:?domain}
CADDY=${2:-pcbuilder-caddy-1}
APP=law_help-app-1

docker inspect "$CADDY" >/dev/null 2>&1 || exit 0   # Caddy is mid-redeploy; try next run
SRC=$(docker inspect "$CADDY" -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}')
[ -f "$SRC" ] || exit 0
changed=

if ! grep -q "^$DOMAIN" "$SRC"; then
  cp "$SRC" "$SRC.bak-law_help"
  printf '\n%s {\n\treverse_proxy law-help-app:8000\n}\n' "$DOMAIN" >> "$SRC"
  if docker exec "$CADDY" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
    changed=1
  else
    cat "$SRC.bak-law_help" > "$SRC"
    echo "$(date -Is) Caddy rejected the law_help block; left the Caddyfile as it was"
    exit 1
  fi
fi

# The app must sit on Caddy's network under the alias Caddy proxies to.
NET=$(docker inspect "$CADDY" -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
if [ -n "$NET" ] && ! docker inspect "$APP" -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | grep -qw "$NET"; then
  docker network connect --alias law-help-app "$NET" "$APP" && echo "$(date -Is) reconnected $APP to $NET"
fi

if [ -n "$changed" ]; then
  # Caddy's admin API may be off ("admin off"), in which case only a restart applies the change.
  docker exec -w /etc/caddy "$CADDY" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
    || docker restart "$CADDY" >/dev/null
  echo "$(date -Is) re-added $DOMAIN to Caddy"
fi
