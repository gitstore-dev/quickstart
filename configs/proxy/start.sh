#!/bin/sh
set -eu

# Caddy creates/renews the localhost certificate and keeps its CA keys in /data.
# Export only the public root to a separate volume for container-side OIDC clients.
rm -f /trust/root.crt
caddy run --config /etc/caddy/Caddyfile --adapter caddyfile &
pid=$!
trap 'kill -TERM "$pid" 2>/dev/null || true' TERM INT
root=/data/caddy/pki/authorities/local/root.crt
while [ ! -s "$root" ]; do
    kill -0 "$pid" 2>/dev/null || { wait "$pid"; exit 1; }
    sleep 1
done
cp "$root" /trust/root.crt.tmp
chmod 644 /trust/root.crt.tmp
mv /trust/root.crt.tmp /trust/root.crt
wait "$pid"
