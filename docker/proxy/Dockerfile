FROM caddy:2.11.7-builder-alpine AS builder
RUN xcaddy build v2.11.7 \
    --with github.com/caddyserver/replace-response@80962887e4c680abfd1b11ce1403fa8cc0fdd3d6

FROM caddy:2.11.7-alpine
COPY --from=builder /usr/bin/caddy /usr/bin/caddy
