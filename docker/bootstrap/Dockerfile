ARG GITSTORE_TAG=latest
FROM ghcr.io/gitstore-dev/api:${GITSTORE_TAG} AS api

# Only credential-volume initialization needs a shell and root permissions.
# Reuse the published binary; no GitStore source build is needed.
FROM alpine:3.23.3
COPY --from=api /app/api /app/api
