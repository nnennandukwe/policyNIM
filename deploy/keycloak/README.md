# Keycloak issuer profile

Use this profile to reproduce PolicyNIM's local OAuth issuer checks for
[issue #97](https://github.com/nnennandukwe/policyNIM/issues/97). The operator has selected Keycloak for Railway deployment. Real ChatGPT connection
acceptance still needs separate evidence.

## Reproduce the local checks

1. Sync the locked Python environment:

   ```bash
   uv sync --locked --group test --group dev
   ```

2. Pull the evaluated Keycloak 26.7.5 image:

   ```bash
   docker pull quay.io/keycloak/keycloak:26.7.5@sha256:37dbaf6f0722c9ec246335f36e1ef8b2e6cb960f7c27e0d8c615121a3d475a85
   ```

3. Run the local issuer test:

   ```bash
   POLICYNIM_RUN_KEYCLOAK_TESTS=1 uv run --no-sync pytest -q tests/test_keycloak_oauth_live.py
   ```

The test imports [the realm template](policynim-realm.json) into a disposable
container. It generates a password, introspection secret, and localhost TLS
certificate, binds listeners to loopback, and verifies the issuer certificate
against its generated trust root. It removes its own container and secret files
on normal teardown, including test failures. A forcibly killed test process may
leave its `policynim-oauth-<random>` container; inspect that exact container before
removing it. Default offline CI excludes this `docker_live` test. No model
credentials or provider workloads are needed.

## Configuration contract

The template uses these placeholders. Supply them only in an isolated issuer
environment you've selected; keep the secret in that environment's secret store.

| Placeholder | Required value |
| --- | --- |
| `POLICYNIM_OAUTH_RESOURCE` | Exact public HTTPS resource URL, including `/mcp` |
| `POLICYNIM_INTROSPECTION_SECRET` | Confidential resource-client secret; PolicyNIM receives the same value as `POLICYNIM_MCP_OAUTH_CLIENT_SECRET` |
| `POLICYNIM_CHATGPT_CLIENT_ID` | Separate pre-registered public OAuth client ID |
| `POLICYNIM_CHATGPT_REDIRECT_URI` | Exact callback copied from ChatGPT's MCP management page |

These are realm-template inputs, separate from PolicyNIM settings. Keycloak
supports environment placeholders during realm import; existing realms need an
operator-controlled update because startup import skips a realm that already
exists. See [Keycloak realm import](https://www.keycloak.org/server/importExport).
The test renders the same placeholders before import, changes the realm name and
token lifetime, and adds a disposable user.

The resource client supports confidential introspection, with login and service
account grants disabled. Its ID and `resource_url` equal the resource URL. The
public MCP client requires PKCE S256, allows one exact callback, and has no
password or implicit grant. Both policy scopes add the resource audience; the
default `basic` scope includes the subject mapper in token introspection. The
template has no user accounts. Registration and account/consent policy remain
operator decisions.

Run Keycloak with `--features=resource-indicators` to enforce `resource` in both
authorization and token requests. This feature remains experimental in the
evaluated version. The test uses `start-dev` on loopback; production needs its
own hardened HTTPS, persistent storage, backups, and issuer lifecycle setup.
See [Keycloak's MCP guide](https://www.keycloak.org/securing-apps/mcp-authz-server).

## Railway production deployment

Build the optimized image from the repository root:

```bash
docker build -f deploy/keycloak/Dockerfile -t policynim-keycloak:26.7.5 .
```

Create a separate empty Railway service named `oauth-keycloak`; keep its source
independent of the PolicyNIM application service. Set its Dockerfile path to
`Dockerfile`, health-check path to `/realms/master`, health-check timeout to
240 seconds, and replica count to one. After staging its variables and resource
limits, deploy only this directory from the repository root:

```bash
npx --yes --package=@railway/cli@5.71.0 railway up deploy/keycloak \
  --path-as-root --service oauth-keycloak --environment production --detach
```

`--path-as-root` excludes the application root's `railway.toml` and
`Dockerfile.railway`. A local `docker build` alone doesn't deploy an image to
Railway. Don't connect this service to the repository root with the application's
build settings. Read back the service's effective Dockerfile and health check,
then wait for its deployment to become healthy; `--detach` only starts deployment.

The image runs `start --optimized` with PostgreSQL and experimental Resource
Indicators enabled. It doesn't import realms or embed credentials. Configure
these runtime variables in Railway's secret store before deploying:

| Variable | Value |
| --- | --- |
| `KC_HOSTNAME` | Full fixed public HTTPS origin of the issuer service |
| `KC_DB_URL` | JDBC PostgreSQL URL using the database's private Railway hostname |
| `KC_DB_USERNAME`, `KC_DB_PASSWORD` | Dedicated issuer database credentials |
| `KC_BOOTSTRAP_ADMIN_USERNAME`, `KC_BOOTSTRAP_ADMIN_PASSWORD` | Initial administrator; remove after creating a permanent operator account |
| `JAVA_OPTS_KC_HEAP` | `-Xms128m -Xmx512m` for the 1 GB service limit |

Railway terminates TLS and forwards requests to port 8080. The image trusts
`X-Forwarded-*` headers, so don't attach another public listener that bypasses
Railway's proxy. Keep the full HTTPS hostname fixed and verify discovery with
forged host/forwarded headers before cutover. Management port 9000 must stay
private. Use `/realms/master` on port 8080 for
Railway's deployment health check, then separately verify the application realm.
The single-replica image uses a local cache; PostgreSQL owns persistent realm,
user and session state. Don't scale replicas without reviewing cache topology.

Use one private PostgreSQL 17 service with a persistent volume mounted at
`/var/lib/postgresql/data`. Pin its image digest, don't create a TCP proxy or
public domain. Railway volume backup schedules require a Pro workspace; check
the account plan before relying on them. Configure an approved backup method
and verify a restore to an isolated database before production cutover. Set limits
before starting either service: Keycloak 1 GB/0.5 vCPU and PostgreSQL
0.5 GB/0.25 vCPU. Track compute, storage, egress and the workspace plan against the approved
operating budget. These limits don't cap storage or egress charges. Don't change the workspace-wide
compute hard limit because it affects unrelated projects.

Import the realm through the authenticated administration API with the template
inputs supplied at runtime. If ChatGPT's callback hasn't been copied yet,
omit its public client during initial import; add it only after the exact
callback is available. Never use a wildcard or guessed redirect URI. Startup
import skips existing realms, so apply later changes through the administration
API rather than expecting a restart to update them. Keep operator registration
disabled until account policy has been selected.

Before changing PolicyNIM, verify HTTPS discovery, PKCE and resource binding,
invalid-token rejection, refresh/revocation, and persistence across an issuer
restart. Use a disposable client/user for automated issuer checks; remove them
afterward. These checks don't require NVIDIA calls.

Stage the complete application configuration as one update:

```text
POLICYNIM_MCP_REQUIRE_AUTH=true
POLICYNIM_MCP_PUBLIC_BASE_URL=https://policynim-production.up.railway.app
POLICYNIM_MCP_OAUTH_ISSUER_URL=<public issuer origin>/realms/policynim
POLICYNIM_MCP_OAUTH_CLIENT_ID=https://policynim-production.up.railway.app/mcp
POLICYNIM_MCP_OAUTH_CLIENT_SECRET=<matching resource-client secret>
POLICYNIM_MCP_OAUTH_ALLOW_LEGACY_KEYS=true
```

The existing beta portal requires explicit legacy-key compatibility during this
migration. After deployment, check readiness, protected-resource discovery,
missing/invalid bearer challenges, and an existing beta key without invoking a
provider-backed tool. OAuth success doesn't prove beta-key compatibility;
record both results independently.

For rollback, restore the prior values of all four `POLICYNIM_MCP_OAUTH_*`
variables together and redeploy the prior verified application revision. If
OAuth was previously unset, remove issuer/client ID/client secret and restore
`ALLOW_LEGACY_KEYS=false` together. Keep `REQUIRE_AUTH=true`, beta portal settings,
existing keys and the authorization database. A failed issuer check must block
cutover; it mustn't weaken application authentication.

See [Keycloak containers](https://www.keycloak.org/server/containers),
[hostname configuration](https://www.keycloak.org/server/hostname), and
[Railway cost control](https://docs.railway.com/pricing/cost-control).

## Evidence boundaries

| Check | Evidence source |
| --- | --- |
| Real PKCE login, code replay and wrong-verifier rejection | Local Keycloak over verified TLS |
| Token audience binding, changed-resource exchange rejection | Local Resource Indicators profile |
| Scope checks for all eight tools, issuer/audience/expiry failures | Offline HTTP matrix; real issuer read-only scope rejection is also exercised |
| Refresh, expired-token refresh, refresh-token revocation, issuer logout and reconnect | Local Keycloak plus real MCP TCP listener |
| Provider token isolation, immutable principal, legacy migration, CLI/stdio | Existing and extended offline tests |
| Production issuer/deployment, ChatGPT registration and linking UI | Pending external acceptance |

The permitted local search uses a deterministic service behind PolicyNIM's real
HTTP authorization path. Successful production search, compilation, preflight,
and evaluation still need a separately approved model-request budget.

## Production acceptance record

Keep these fields with the PR/deployment evidence; record outcomes without raw
tokens, authorization codes, passwords, cookies, or introspection payloads:

| Field | Required record |
| --- | --- |
| Application | Exact source SHA, artifact identity, deployment ID and deployed SHA |
| Issuer/client | Public issuer URL, version/image digest, registration method, client ID, token-auth method and exact callback |
| Resource/scopes | Exact resource URL and permitted/denied operation matrix |
| Lifecycle | PKCE, refresh, expiry, revocation, issuer logout, portal logout and recovery outcomes |
| ChatGPT | Product/client version or observation date, connection result, linking/step-up result and tested tool outcomes |
| Workload budget | Approved provider-request ceiling, actual request count and sanitized failure classes |

Keep #97 open while any required external acceptance remains unverified. Directory
packaging, legal pages, recordings, and submission have their own workstream.
