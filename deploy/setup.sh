#!/usr/bin/env bash
# One-time setup of law_help next to an existing Caddy container.
# Usage: sudo bash setup.sh law.rigcheck.in [caddy-container]
set -euo pipefail
DOMAIN=${1:?domain, e.g. law.rigcheck.in}
CADDY=${2:-pcbuilder-caddy-1}
DIR=/data/law_help

if [ -d "$DIR/.git" ]; then git -C "$DIR" pull -q; else git clone -q -b main https://github.com/prittamdh/law_help "$DIR"; fi
cd "$DIR/deploy"

if [ ! -f .env ]; then
  NET=$(docker inspect "$CADDY" -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' | awk '{print $1}')
  printf 'POSTGRES_PASSWORD=%s\nCADDY_NETWORK=%s\n' "$(openssl rand -hex 16)" "$NET" > .env
  chmod 600 .env
fi

docker compose -p law_help up -d --build </dev/null
echo "Waiting for the database..."
until docker compose -p law_help exec -T db pg_isready -U law -d law_help </dev/null >/dev/null 2>&1; do sleep 2; done
docker compose -p law_help exec -T app python -c "from law_help import db; db.init_schema(db.connect())" </dev/null

# Add a site block to Caddy's config (backed up first), validate, reload.
SRC=$(docker inspect "$CADDY" -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy/Caddyfile"}}{{.Source}}{{end}}{{end}}')
if [ -z "$SRC" ]; then
  SRC=$(docker inspect "$CADDY" -f '{{range .Mounts}}{{if eq .Destination "/etc/caddy"}}{{.Source}}{{end}}{{end}}')
  [ -n "$SRC" ] && SRC="$SRC/Caddyfile"
fi
if [ -z "$SRC" ] || [ ! -f "$SRC" ]; then
  echo "Could not find the Caddyfile on the host. Add this to it by hand and reload Caddy:"
  printf '\n%s {\n\treverse_proxy law-help-app:8000\n}\n' "$DOMAIN"
  exit 1
fi
if ! grep -q "^$DOMAIN" "$SRC"; then
  cp "$SRC" "$SRC.bak-law_help"
  printf '\n%s {\n\treverse_proxy law-help-app:8000\n}\n' "$DOMAIN" >> "$SRC"
fi
if docker exec "$CADDY" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
  # Caddy's admin API may be off ("admin off"), in which case only a restart applies the change.
  docker exec -w /etc/caddy "$CADDY" caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile 2>/dev/null \
    || docker restart "$CADDY" >/dev/null
  echo "Done: https://$DOMAIN"
else
  [ -f "$SRC.bak-law_help" ] && cat "$SRC.bak-law_help" > "$SRC"
  docker exec "$CADDY" caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile || true
  echo "Caddy rejected the new config; restored the original. Nothing else changed."
  exit 1
fi
