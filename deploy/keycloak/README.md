# Keycloak evaluation profile

Use this profile to reproduce PolicyNIM's local OAuth issuer checks for
[issue #97](https://github.com/nnennandukwe/policyNIM/issues/97). Production issuer
selection and real ChatGPT connection acceptance are pending.

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
