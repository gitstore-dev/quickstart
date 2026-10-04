# GitStore quickstart

Docker Compose overlays for running [GitStore](https://github.com/gitstore-dev/GitStore) locally
against different profiles, without building from source.

## Prerequisites

- Docker and Docker Compose v2
- `make`

On Windows, run everything from inside [WSL2](https://learn.microsoft.com/windows/wsl/install) —
the Makefile relies on a POSIX shell and `make`, neither of which `cmd.exe`/PowerShell provide.
Docker Desktop for Windows already uses the WSL2 backend by default, so this doesn't need any
extra setup beyond what Docker Desktop requires.

## Quick start

```sh
cp env.example .env
make up
```

This brings up the `local` profile: `git-service`, `api`, `controller-manager`, a single
static-users admin account (`admin` / `admin123`), and the in-memory (`memdb`) datastore. The API
is at `http://localhost:4000/graphql`, raw git-over-HTTP at `http://localhost:9000`.

```sh
make down   # stop, keep volumes
make clean  # stop and remove volumes (destroys repo data, signing keys, demo DB state)
make logs
make ps
```

## Profiles

The stack is selected with three independent `make` variables, and they combine freely.
`compose.local.yml` (static-users auth + the one-time credential bootstrap) is always
included — it's the baseline every combination builds on, never swapped out.

| Variable    | Values            | Default | Meaning                                             |
|-------------|--------------------|---------|------------------------------------------------------|
| `DATASTORE` | `memdb`, `scylla`  | `memdb` | Storage backend                                      |
| `PROFILE`   | `single`, `cluster`| `single`| Only matters when `DATASTORE=scylla`                 |
| `IDENTITY`  | `none`, `oidc`     | `none`  | Layers a demo OIDC IdP (Hydra+Kratos) in front of auth|

Examples:

```sh
make up                                             # local: memdb + static-users
make up DATASTORE=scylla                           # scylla: single-node ScyllaDB + static-users
make up DATASTORE=scylla PROFILE=cluster            # scylla cluster: 3-node ScyllaDB + static-users
make up IDENTITY=oidc                               # oidc: memdb + static-users + demo Hydra/Kratos IdP
make up DATASTORE=scylla IDENTITY=oidc              # scylla + oidc together
make up DATASTORE=scylla PROFILE=cluster IDENTITY=oidc   # scylla cluster + oidc together
```

Each axis contributes its own override-only `gitstore.toml` under `configs/` — `api`,
`controller-manager`, and `git-service` are started with `--config-file` passed once per
file (base first, then each axis's override), and GitStore merges them
([gitstore-dev/GitStore#442](https://github.com/gitstore-dev/GitStore/pull/442)). `git-service`
only ever gets the base file — its CLI doesn't accept `--config-file` more than once, and it
only reads settings no profile override changes.

### local (default)

Memdb datastore, static-users auth. On first `make up`, `credential-bootstrap` generates the
service-account signing keys and `serviceaccount-enrollment` registers `controller-manager`'s
service account with `api` — both one-shot, both re-run safely on every `up`.

Demo login: `admin` / `admin123` (`configs/users.yaml`). Change the password with
`make hash-user-password PASSWORD=...` in GitStore's own repo and update the hash in
`configs/users.yaml`.

### scylla / scylla cluster

`make up DATASTORE=scylla` adds a single ScyllaDB node (`scylladb/scylla:2026.1`,
developer-mode) and creates the `gitstore` keyspace (RF=1) before `api`/`controller-manager`
start. `make up DATASTORE=scylla PROFILE=cluster` runs a 3-node cluster instead
(`NetworkTopologyStrategy`, RF=3). Same static-users auth as `local`.

### oidc

`make up IDENTITY=oidc` adds a self-contained demo identity provider: Ory Hydra (OAuth2/OIDC
issuer, `http://localhost:4444`), Ory Kratos (identity + self-service UI at
`http://localhost:4455`), and a small bridge service wiring Kratos's login/consent decisions
into Hydra. There's no pre-seeded demo user — register one yourself at
`http://localhost:4455/registration` (check `http://localhost:4436` for the dev mail catcher if
a flow asks for email verification). The authn chain still includes `static-users` and the
service-account chain entries, so the `local` bootstrap still runs underneath.

Identities are matched by `preferred_username` (what you registered with — e.g. `demo-user`),
not the raw Kratos subject UUID, specifically so you can recognize yourself in logs/tokens and
know what to type into `policy.yaml`.

**Granting yourself any permission is a manual step — this is a real GitStore limitation, not
something this quickstart works around.** GitStore deliberately never derives roles from OIDC
claims (importing a `roles` claim would let any issuer mint local admin with no `role_bindings`
entry — a security decision, not an oversight; see GitStore's
`specs/059-optional-oidc-provider/spec.md`). A freshly self-registered user authenticates fine
and gets zero permissions (`default_deny: true`, no matching binding). To grant yourself access
after registering:

```yaml
# configs/policy.yaml
role_bindings:
  your-username:   # whatever you registered with (preferred_username)
    - admin
```

Then apply it — policy changes are picked up on `SIGHUP`, not automatically on file save:

```sh
docker compose -p gitstore kill -s HUP api
```

(Confirmed by testing: a background file-watcher does *not* exist; a bad edit is safely
rejected with an error log and the previous policy stays active, so this is safe to experiment
with without taking the API down.)

Requires secrets in `.env` with no defaults (see `env.example`) — generate each with
`openssl rand -hex 32` (hex, not base64 — two of these land directly in an unencoded postgres
DSN, and base64's `/`/`+` breaks that).

`oidc-bridge` ships from a separate optional-images pipeline that hasn't cut a versioned/`latest`
tag yet — it currently defaults to `GITSTORE_OIDC_BRIDGE_TAG=main`, independent of `GITSTORE_TAG`.
Override it in `.env` once a real release tag exists.

## Configuration

`configs/` is mounted read-only into every container at `/etc/gitstore`:

- `configs/gitstore.toml` — base config (memdb + static-users), always loaded first
- `configs/scylla/gitstore.scylla.toml`, `configs/scylla/gitstore.cluster.toml` — scylla
  datastore overrides (merged on top of the base when `DATASTORE=scylla`)
- `configs/oidc/gitstore.oidc.toml`, `configs/oidc/hydra/`, `configs/oidc/kratos/` — oidc
  override (merged on top of the base when `IDENTITY=oidc`)
- `configs/users.yaml`, `configs/policy.yaml` — shared static-users directory and RBAC policy

## Image tags

All images default to the `latest` tag on `ghcr.io/gitstore-dev/*`, which tracks GitStore's
newest build pre-GA. After the first stable (non-prerelease) release ships, `latest` stops
moving on prereleases — pin `GITSTORE_TAG` in `.env` once you depend on this for more than a
quick local test.

## Known limitations

- The optional `admin` backoffice UI image isn't wired up here yet — revisit once it's more
  mature.

## Troubleshooting

**`scylla-N` fails under `PROFILE=cluster` with an AIO/seastar error**
(`Could not initialize seastar: ... Your system does not satisfy minimum AIO requirements`):
running three concurrent ScyllaDB processes can exceed the host's `fs.aio-max-nr`. The cluster
nodes already run with `--smp=${SCYLLA_CLUSTER_SMP:-1}
--max-networking-io-control-blocks=${SCYLLA_CLUSTER_MAX_NETWORKING_IO_CONTROL_BLOCKS:-2048}` —
GitStore's own documented values specifically chosen so three nodes fit under Docker Desktop's
typical default ceiling without touching the host. If you still hit this (e.g. a lower default
on your machine, or other containers competing for the same limit), lower
`SCYLLA_CLUSTER_MAX_NETWORKING_IO_CONTROL_BLOCKS` further in `.env` (e.g. `1024`), or as a last
resort raise the Docker Desktop VM's `fs.aio-max-nr` directly (not something GitStore itself
documents or endorses — host/Docker-Desktop-specific, works on both Intel and Apple Silicon):

```sh
docker run --rm --privileged --pid=host alpine sh -c \
  "apk add --no-cache util-linux >/dev/null && nsenter -t 1 -m -u -n -i sysctl -w fs.aio-max-nr=1048576"
```
