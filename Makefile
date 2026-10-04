# Profile axes, mirroring GitStore's own root Makefile vocabulary:
#   DATASTORE = memdb | scylla          (default: memdb)
#   PROFILE   = single | cluster        (only relevant when DATASTORE=scylla)
#   IDENTITY  = none | oidc             (default: none)
#
# compose.local.yml (static-users auth + credential bootstrap) is always part of
# the stack - every combination builds on it, it's never swapped out.
#
# Each axis contributes its own override-only gitstore.toml, combined via repeated
# --config-file flags (GitStore merges them: github.com/gitstore-dev/GitStore#442).
# All combinations, including DATASTORE=scylla + IDENTITY=oidc, are supported.
DATASTORE ?= memdb
PROFILE   ?= single
IDENTITY  ?= none

export GITSTORE_TAG ?= latest

SCYLLA_COMPOSE_FILE    = $(if $(filter cluster,$(PROFILE)),compose.scylla.cluster.yml,compose.scylla.yml)
SCYLLA_CONFIG_FILE     = $(if $(filter cluster,$(PROFILE)),/etc/gitstore/scylla/gitstore.cluster.toml,/etc/gitstore/scylla/gitstore.scylla.toml)
DATASTORE_COMPOSE_FILE = $(if $(filter scylla,$(DATASTORE)),-f $(SCYLLA_COMPOSE_FILE),)
IDENTITY_COMPOSE_FILE  = $(if $(filter oidc,$(IDENTITY)),-f compose.oidc.yml,)

DATASTORE_CONFIG_FILE = $(if $(filter scylla,$(DATASTORE)), $(SCYLLA_CONFIG_FILE),)
IDENTITY_CONFIG_FILE  = $(if $(filter oidc,$(IDENTITY)), /etc/gitstore/oidc/gitstore.oidc.toml,)
export CONFIG_FILES = /etc/gitstore/gitstore.toml$(DATASTORE_CONFIG_FILE)$(IDENTITY_CONFIG_FILE)

COMPOSE = docker compose --profile local -f compose.yml -f compose.local.yml $(DATASTORE_COMPOSE_FILE) $(IDENTITY_COMPOSE_FILE)

.PHONY: validate up down logs ps config pull clean

validate:
	@case "$(DATASTORE)" in memdb|scylla) ;; *) echo "DATASTORE must be 'memdb' or 'scylla'" >&2; exit 2;; esac
	@case "$(PROFILE)" in single|cluster) ;; *) echo "PROFILE must be 'single' or 'cluster'" >&2; exit 2;; esac
	@case "$(IDENTITY)" in none|oidc) ;; *) echo "IDENTITY must be 'none' or 'oidc'" >&2; exit 2;; esac

up: validate ## Start the stack for the selected DATASTORE/PROFILE/IDENTITY combo
	$(COMPOSE) up -d

down: validate ## Stop the stack, keep volumes
	$(COMPOSE) down

logs: validate ## Tail logs for the running combo
	$(COMPOSE) logs -f

ps: validate ## List containers for the running combo
	$(COMPOSE) ps

config: validate ## Print the fully resolved compose config for the combo
	$(COMPOSE) config

pull: validate ## Pull images for the selected combo without starting it
	$(COMPOSE) pull

clean: validate ## Stop the stack and remove its volumes (destroys repo data, signing keys, demo DB state)
	$(COMPOSE) down -v
