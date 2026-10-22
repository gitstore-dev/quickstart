# GitStore quickstart

Docker Compose overlays for running [GitStore](https://github.com/gitstore-dev/GitStore) locally
against different profiles, without building from source.

## Prerequisites

- Docker and Docker Compose v2.24.4 or later (the proxy overlays use Compose reset tags)
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

The stack is selected with four independent `make` variables, and they combine freely.
`compose.local.yml` (static-users auth + the one-time credential bootstrap) is always
included — it's the baseline every combination builds on, never swapped out.

| Variable    | Values            | Default | Meaning                                             |
|-------------|--------------------|---------|------------------------------------------------------|
| `DATASTORE` | `memdb`, `scylla`  | `memdb` | Storage backend                                      |
| `PROFILE`   | `single`, `cluster`| `single`| Only matters when `DATASTORE=scylla`                 |
| `IDENTITY`  | `none`, `oidc`     | `none`  | Layers a demo OIDC IdP (Hydra+Kratos) in front of auth|
| `PROXY`     | `none`, `caddy`    | `none`  | Opt-in HTTPS front door on host ports 80 and 443     |

Examples:

```sh
make up                                             # local: memdb + static-users
make up DATASTORE=scylla                           # scylla: single-node ScyllaDB + static-users
make up DATASTORE=scylla PROFILE=cluster            # scylla cluster: 3-node ScyllaDB + static-users
make up IDENTITY=oidc                               # oidc: memdb + static-users + demo Hydra/Kratos IdP
make up DATASTORE=scylla IDENTITY=oidc              # scylla + oidc together
make up DATASTORE=scylla PROFILE=cluster IDENTITY=oidc   # scylla cluster + oidc together
make up PROXY=caddy                                 # HTTPS GraphQL and Git
make up PROXY=caddy IDENTITY=oidc                   # HTTPS including identity and mail
```

Use the same variables for `down`, `logs`, `ps`, `config`, and `clean`. Run `make down`
with the old selection before switching profiles or proxy mode; volumes are retained.

Each configuration axis contributes an override-only `gitstore.toml` under `configs/`.
`api` and `controller-manager` receive `--config-file` once per file, and GitStore merges
them in order: the base file first, followed by the selected overrides. `git-service`
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

Kratos connects to MailSlurper using implicit SMTP TLS (`smtps://`). The mail catcher's
bundled development certificate is accepted only for that SMTP connection.

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

`oidc-bridge` ships from a separate optional-images pipeline from the core images, so it has
its own `GITSTORE_OIDC_BRIDGE_TAG` (default `latest`) independent of `GITSTORE_TAG` — its tags
can drift from the core images' release cadence.

### HTTPS front door (opt-in)

`make up PROXY=caddy` builds and starts Caddy. It is the only container publishing host
ports in this mode: HTTP on **80** redirects to HTTPS on **443**, preserving request methods.
These ports must be available. Without `PROXY=caddy`, the direct ports above remain in use.
Database and gRPC ports are internal in every mode; database tools can run with
`docker compose exec` on the relevant service.

| Public HTTPS URL | Upstream |
|------------------|----------|
| `https://localhost/admin/api` | API GraphQL on `api:4000/graphql`, including subscriptions |
| `https://localhost/<namespace>/<repository>[.git]` | Git Smart HTTP on `api:9000` |
| `https://localhost/sso/` | Hydra public API / OIDC issuer |
| `https://localhost/user/` | Kratos self-service UI (`/user/registration`, `/user/login`, etc.) |
| `https://localhost/user/api/` | Kratos public API |
| `https://localhost/login`, `https://localhost/consent` | OIDC bridge callbacks |
| `https://localhost/mail/` | MailSlurper UI (its HTTP API is under `/mail/api/`) |
| `https://localhost/admin/sso/clients` | Hydra admin API, operator Basic Auth required |
| `https://localhost/admin/user/identities` | Kratos admin API, operator Basic Auth required |

Identity/mail routes require `IDENTITY=oidc`. Bare `/sso`, `/user`, and `/mail` redirect to
their trailing-slash forms. Other unmatched routes return 404. Git clone/fetch/push use the
usual `info/refs`, `git-upload-pack`, and `git-receive-pack` paths below the repository URL.
The service prefixes above are reserved; repositories under them cannot use those paths.

`/admin/api` preserves GitStore's own authentication and RBAC. Its upstream currently remains
`/graphql`; when GitStore introduces `/admin/graphql` and `/storefront/graphql`, only the
admin rewrite needs changing here. Storefront, admin UI, and SSH ingress are not included.
Internal enrollment and controller GraphQL URLs remain unchanged.

#### Local certificate trust

Caddy generates and renews a localhost certificate using its own local CA. Its CA and keys
persist in the `proxy-data` volume. Only the **public** CA certificate is copied into
`proxy-ca`, which the API container trusts for HTTPS OIDC discovery, JWKS, and userinfo.
Controller-manager uses service-account authentication against the internal API and does not
need OIDC issuer settings, host routing, or the proxy CA.
HTTPS clients verify Caddy's certificate against that exported CA.

Export the public CA after startup:

```sh
make proxy-ca PROXY=caddy # add IDENTITY=oidc if selected
curl --cacert /tmp/gitstore-local-ca.crt https://localhost/admin/api \
  -H 'Content-Type: application/json' -d '{"query":"{ __typename }"}'
git -c http.sslCAInfo=/tmp/gitstore-local-ca.crt clone https://localhost/my-namespace/my-repository.git
```

For browser-based registration/login, import that certificate into your local trusted root
store (and the browser's certificate store if it uses a separate one). Trust is a manual
host-side step; trusting the CA inside the Caddy container does not trust it on your computer.
`make down PROXY=caddy` retains the CA. `make clean PROXY=caddy` removes it with the other
selected volumes; export and trust the replacement certificate after the next startup.

#### Dedicated identity operator

Before using `PROXY=caddy IDENTITY=oidc`, generate a password hash interactively:

```sh
docker run --rm -it caddy:2.11.7-alpine caddy hash-password
```

Put the username and resulting hash in `.env`, **single-quoting the hash** to preserve its
dollar signs:

```dotenv
GITSTORE_PROXY_OPERATOR_USERNAME=operator
GITSTORE_PROXY_OPERATOR_PASSWORD_HASH='$2a$...'
```

There is no default password. Missing operator settings prevent this combination from
starting. This account is separate from GitStore's demo admin and from registered Kratos
users. It grants full access to both identity admin APIs; no additional roles are modeled.
The proxy strips the operator Authorization header before forwarding to Ory.

```sh
# No credentials: 401. With --user operator, curl prompts for the password.
curl --cacert /tmp/gitstore-local-ca.crt https://localhost/admin/sso/clients
curl --cacert /tmp/gitstore-local-ca.crt --user operator https://localhost/admin/user/identities
```

Bridge and bootstrap containers still call the internal admin APIs directly, so operator
credentials do not enter their configuration. The bridge itself needs browser-accessible
`/login` and `/consent` routes, but has no host port binding in proxy mode.

The proxy image includes a pinned `caddyserver/replace-response` module because MailSlurper
hard-codes root-relative UI URLs and an API port. Replacements apply only to its UI HTML
and application JavaScript; email bodies and attachments pass through unchanged. The first
proxy build requires access to the Go module registry.

#### Validation

```sh
python3 -m unittest discover -s tests -v
make config PROXY=caddy IDENTITY=oidc
```

The automated Compose checks cover all datastore/identity/proxy combinations, published
ports, and missing operator credentials without starting containers. For live verification,
also check GraphQL queries/subscriptions, Git clone/fetch/push, OIDC registration and login,
an authenticated GraphQL request, and mail inbox/message/attachment loading through HTTPS.

The optional live HTTPS tests use `GITSTORE_TEST_PROXY_CA` (the exported certificate path).
Set `GITSTORE_TEST_OPERATOR_USERNAME` and `GITSTORE_TEST_OPERATOR_PASSWORD` as well to check
successful operator access. Set `GITSTORE_TEST_ADMIN_PASSWORD` (and optionally
`GITSTORE_TEST_ADMIN_USERNAME`, default `admin`) to verify native GraphQL login and bearer
authentication too. Run `python3 -m unittest discover -s tests -p test_proxy_live.py -v`.
They check redirects, denied/allowed admin access, discovery/JWKS, registration, the OIDC
login redirect chain, and MailSlurper UI/API routing without creating users or repositories.

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
