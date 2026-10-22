# Hosted authorization foundation

PolicyNIM's hosted MCP endpoint can act as a resource server: an external
identity provider signs callers in and issues access tokens; PolicyNIM checks
those tokens before permitting work. NVIDIA credentials stay on the server and
aren't used as client credentials.

Implementation and acceptance are tracked in [issue #97](https://github.com/nnennandukwe/policyNIM/issues/97).
The user selected Keycloak for local evaluation on October 22, 2026. The opt-in
[issuer test](../tests/test_keycloak_oauth_live.py) crosses real Keycloak TLS and
MCP TCP boundaries with generated fixture credentials and a deterministic search
service. Its evidence covers local issuer interoperability with zero NVIDIA
requests. Production issuer/client selection, deployment, and real ChatGPT
connection acceptance remain pending.

OAuth stays disabled until the operator configures it. OAuth-only mode doesn't
require a beta authentication database. Hosted operation quotas apply to OAuth
principals and compatible beta/operator keys. Live model checks need a separately
approved request budget.

## Evaluated issuer and client configuration

The evaluated profile uses **Keycloak 26.7.5**, **MCP Python SDK 2.2.0**,
**MCP 2026-07-28**, and a pre-registered public client with Authorization Code
and PKCE S256. The [realm template](../deploy/keycloak/policynim-realm.json)
separates that client from the confidential introspection resource client. The
[test guide](../deploy/keycloak/README.md) pins the image digest and names the
exact local configuration and remaining production decisions.

Resource Indicators support requires Keycloak's experimental
`--features=resource-indicators` flag. The evaluated profile enables it so the
issuer validates `resource` during authorization and token exchange. Accepting
that experimental feature for production requires an operator decision. CIMD
(Client ID Metadata Documents) and dynamic registration aren't exercised by this
profile; production setup must explicitly select its registration method.

Sources checked October 22, 2026:
[MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization),
[OpenAI authentication](https://developers.openai.com/plugins/build/auth),
[Keycloak MCP requirements](https://www.keycloak.org/securing-apps/mcp-authz-server),
and [Keycloak administration](https://www.keycloak.org/docs/26.7.5/server_admin/index.html).

Before enabling OAuth, the operator must complete these steps in an existing
issuer they own:

1. Choose the exact public resource URL, such as `https://policy.example/mcp`.
   Create a confidential Keycloak resource client whose client ID equals that
   URL and permits `client_secret_post` authentication. Keep its introspection secret in the deployment's secret manager.
2. Add optional client scopes `policies:read` and `policies:evaluate`. Include
   each scope in the token scope claim. Attach an Audience mapper to each scope
   whose Included Custom Audience equals the exact resource URL. Include the
   audience in token introspection. Since the resource client's ID equals that
   audience, Keycloak's introspection audience check can remain enabled.
3. Register the MCP client as a public client with Standard Flow enabled,
   PKCE S256 required, and its exact approved callback URI. Disable implicit and
   password grants. Attach the two optional client scopes. Preload the registered
   client ID in a client that supports pre-registration. For ChatGPT, copy the
   exact callback URI from its MCP management page; don't guess a stable callback
   or allow wildcard redirects. Its registration and token-auth method must match
   the selected issuer configuration. The resource client's secret
   must never be installed in the public MCP client.
4. Preserve the `basic` default client scope with the Subject (`sub`) mapper
   enabled for access tokens and introspection. The local test initially rejected
   tokens when this mapper was missing. Confirm discovery advertises PKCE S256,
   and verify any advertised OIDC scopes are enabled for the registered client.
   Confirm the realm's OpenID Connect discovery document reports the exact issuer
   and its authorization/token endpoints. Configure short-lived access tokens,
   consent, user access, refresh policy, and revocation in the issuer. The
   introspection response must include `active`, `iss`, `sub`, `client_id`, `aud`,
   integer `exp`, string `scope`, and `token_type=Bearer`. An optional `nbf` must
   already have passed. ID tokens aren't accepted as API credentials.
5. Configure the exact resource client's `resource_url` attribute, its Audience
   mapper, and Resource Indicators as shown in the evaluated template. Set the
   PolicyNIM environment settings below and perform the live acceptance
   checks before directing users to OAuth.

```bash
POLICYNIM_MCP_REQUIRE_AUTH=true
POLICYNIM_MCP_PUBLIC_BASE_URL=https://policy.example
POLICYNIM_MCP_OAUTH_ISSUER_URL=https://identity.example/realms/policynim
POLICYNIM_MCP_OAUTH_CLIENT_ID=https://policy.example/mcp
POLICYNIM_MCP_OAUTH_TIMEOUT_SECONDS=5
POLICYNIM_MCP_OAUTH_MAX_CONCURRENT_REQUESTS=64
POLICYNIM_MCP_OAUTH_REQUEST_BODY_TIMEOUT_SECONDS=30
POLICYNIM_MCP_OAUTH_ALLOW_LEGACY_KEYS=false
```

Set `POLICYNIM_MCP_OAUTH_CLIENT_SECRET` through the secret manager. Don't put it
in a client command, committed file, URL, or support output. Issuer/client
settings default to absent values. Supplying a partial configuration fails startup.
Issuer URLs require HTTPS without credentials, query, fragment, or trailing
slash. Public resource hosting also requires HTTPS. The change preserves existing settings precedence: direct constructor values, process environment, then dotenv/config
sources as defined by `Settings`.

The verifier posts to the fixed realm endpoint
`<issuer>/protocol/openid-connect/token/introspect` with `client_secret_post` form
authentication over HTTPS. HTTPX encodes the client ID and secret as form fields;
they aren't placed in the URL or forwarded to NVIDIA. It doesn't follow redirects, use environment proxies, accept
token-selected endpoints, or cache successful token validation. The total introspection deadline is
bounded between greater than zero and 30 seconds; it defaults to five seconds.
A fresh HTTP client closes after each introspection, including failure.

A separate HTTP guard admits at most 64 concurrent requests carrying an
Authorization header, including headers sent to public routes where the SDK also
authenticates them. The configured limit can range from 1 to 256. It holds a slot
through response cleanup and rejects excess requests with HTTP `429`,
`error=auth_busy` and `Retry-After: 1` before contacting the issuer. Public health
and metadata without an Authorization header remain accessible. This request cap operates separately from Foundation's model-operation cap and
from principal work-unit accounting. See [operation limits](hosted-beta-operations.md#principal-operation-limits).

## Discovery and operation permissions

`GET /.well-known/oauth-protected-resource/mcp` publishes the exact `/mcp`
resource, external authorization server, header bearer method, and supported
scopes. It doesn't publish client secrets or provider configuration. A client
uses the issuer's own discovery/login/token endpoints to obtain its access token.

| Request | Required authority |
| --- | --- |
| MCP discovery, initialization, ping | Any currently valid access token |
| `policy_search` | `policies:read` |
| `policy_compile`, `policy_preflight`, `policy_preflight_evidence` | `policies:read policies:evaluate` |
| `eval_submit`, `eval_inspect`, `eval_cancel`, `eval_result` | `policies:read policies:evaluate`, plus job ownership |
| Policy catalog/document/chunk resources and review prompt | `policies:read` |
| Evaluation report resources | `policies:read policies:evaluate`, plus job ownership |
| `/healthz` | Public, sanitized readiness only |

Missing, expired, inactive, malformed, foreign-issuer or foreign-audience tokens
receive HTTP `401` with `WWW-Authenticate: Bearer` and `resource_metadata`.
The HTTP boundary caps authenticated POST bodies at 1 MiB before dispatch.
Incomplete bodies receive HTTP `408 request_timeout` after 30 seconds by default,
releasing their HTTP admission slot. The body deadline can range above zero up
to 300 seconds.
Insufficient tool scopes receive HTTP `403` with `error="insufficient_scope"`,
the complete required `scope` set, and the same metadata URL. Both `/mcp` and
`/mcp/` remain behind authorization. Host/Origin checks run before authentication.

The server explicitly sets the SDK's `validate_token_resource=True`. The verifier checks exact
issuer and audience membership before returning that resource to the SDK.
The server introspects each protected HTTP request again. Each tool invocation checks
its scopes and expiry before admission/provider allocation, and search/preflight
services check again before their pipelines. A frozen principal carries issuer,
subject, client ID, scopes, credential source and expiry into service work.
It contains no access token. Local CLI and stdio MCP operations remain trusted
local calls, even with hosted auth configured. OAuth authentication and scope
denials retain sanitized `mcp.auth` events without credentials or request bodies.

Each OAuth `tools/list` response declares the operation's scopes in both
`securitySchemes` and `_meta.securitySchemes`. SDK 2.2 sieves unknown core tool
fields, so public SDK middleware adds the extension after that sieve. Legacy-key
and local stdio servers retain their existing declarations. Scope/dispatch-expiry
denials for tool requests retain HTTP 403/401 and return a JSON-RPC tool error
result with `_meta["mcp/www_authenticate"]` matching the header, including a
sanitized `error_description`. Clients that act on transport status can use the
header; real ChatGPT linking and step-up UI still need acceptance evidence.

The shared public corpus doesn't grant access to another user's evaluation jobs.
All four job tools and report resources enforce owner identity separately from
scope checks. The service permission function denies unknown protected operations.

## Existing-key migration and recovery

Existing deployments with no OAuth issuer setting keep their current bearer-key
behavior. GitHub sign-in at `/beta` creates a portal session and beta API key;
it doesn't obtain an MCP OAuth token.

1. Preserve the existing auth database and deployment settings. Configure and
   test the external issuer in a separate deployment before migration.
2. For a staged migration, set `POLICYNIM_MCP_OAUTH_ALLOW_LEGACY_KEYS=true`.
   Existing configured operator keys and active beta keys can then use `/mcp`
   alongside issuer tokens. Legacy credentials receive the two current tool
   scopes and are explicitly identified as `operator_key` or `beta_key`.
3. Move clients to issuer tokens. Existing manual CLI integrations may still
   send `Authorization: Bearer` from `POLICYNIM_TOKEN`; obtaining/refreshing an
   OAuth token is the client's responsibility. Never export an NVIDIA key or
   the introspection client secret as `POLICYNIM_TOKEN`.
4. Disable beta signup before setting compatibility to `false`; the portal
   issues old keys and would otherwise give users unusable credentials.
   Removing compatibility rejects old keys without deleting their stored
   records. Remove operator key values from deployment settings when finished.
5. For recovery, restore a known-good issuer/client configuration. An operator
   can deliberately re-enable compatibility with a retained key while repairing
   the issuer. Preserve audit history. Don't disable auth or Host/Origin checks.

In compatibility mode, a bounded read-only lookup recognizes locally issued
key hashes, including revoked keys, before any issuer request. Recognition grants
no access; the existing transactional authenticator still checks revocation and
account status. Work acceptance checks the key and account in the transaction that
reserves units. Later execution activation rechecks them while acquiring a slot,
without reserving units again. Known denied keys never reach the issuer. Unknown opaque
tokens, including ones starting with `pnm_`, can still be verified by the issuer.
If local recognition storage fails, unrecognized tokens can continue to issuer
verification; local authorization never succeeds from a failed lookup.

In compatibility mode, beta key rotation revokes the previous key immediately;
operator revoke and account suspension block subsequent authentication.
Authentication and discovery don't reserve units in either mode. Interactive
expensive operations reserve one unit at admission; evaluation submission reserves
its validated case/configuration weight before queuing. Failed or cancelled
accepted work keeps its units. Rotation preserves account usage.
[Operation limits and recovery](hosted-beta-operations.md#principal-operation-limits)
describe configuration, outcomes and the single-instance boundary.

Portal logout clears the portal cookie; it doesn't revoke beta keys or issuer
tokens. Revoke beta keys through the existing operator command or rotate them in
the portal. Revoke issuer tokens/sessions through Keycloak and remove tokens from
the client store. Introspection observes issuer revocation once the issuer reports
`active=false`; its semantics must be verified live. Already admitted synchronous
provider work drains under the existing cancellation/cleanup contract. Revocation
affects subsequent requests and doesn't recall work already admitted.

## Credential roles

| Credential or identity | Owner and purpose |
| --- | --- |
| Portal GitHub OAuth client/session | Signs users into `/beta` and issues legacy beta keys |
| Beta/operator bearer key | Explicit compatibility credential; issued without MCP OAuth |
| MCP access/refresh token | Issuer-issued user authority, held by the MCP client |
| Introspection resource client | PolicyNIM's confidential verifier identity; secret stays server-side |
| ChatGPT's registered client | Starts user login and exchanges codes with PKCE; separate from the resource client |
| NVIDIA credential | Server-owned model access; never an MCP client token |

## Acceptance evidence still required

Before closing #97, record the actual issuer/client versions and deployment
revision, then use a real client to discover the issuer, complete PKCE login,
obtain a resource-bound token, and call the permitted tools. Verify wrong audience,
issuer, expiry and scope rejection without provider work; token refresh, logout,
revocation and recovery; and sanitized public readiness. Live model checks require
a separately approved request budget; offline test results don't authorize
provider traffic.
Record source tests, issuer interoperability, deployment and live provider results
separately. Keep #97 open while that external evidence is unavailable.
