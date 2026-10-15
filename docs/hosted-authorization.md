# Hosted authorization foundation

PolicyNIM's hosted MCP endpoint can act as a resource server: an external
identity provider signs callers in and issues access tokens; PolicyNIM checks
those tokens before permitting work. NVIDIA credentials stay on the server and
aren't used as client credentials.

This foundation contributes to [issue #97](https://github.com/nnennandukwe/policyNIM/issues/97).
It doesn't complete that issue. No operator-owned external issuer or registered
MCP client was available during implementation. Offline tests cover HTTP and
service behavior, including a real local TCP connection, with a deterministic
introspection endpoint double. They don't prove an external login/token flow.
OAuth remains disabled on existing deployments until an operator configures it.
OAuth-only mode doesn't initialize or require a beta authentication database.
OAuth operation quotas aren't implemented yet. Don't enable public OAuth access
until #98 supplies principal accounting; use a separately bounded model-request
budget for operator-led issuer acceptance checks.

## Selected issuer and client configuration

The selected operator setup uses Keycloak's documented scope/audience mapping
and a pre-registered native client using Authorization Code with PKCE S256.
PolicyNIM implements RFC 7662 introspection against that Keycloak realm.
It doesn't host login, issue tokens, proxy token exchanges, or register clients.

Reviewed on October 15, 2026: MCP Python SDK **2.2.0** (pinned in the lockfile),
MCP **2026-07-28**, and Keycloak **26.7.5** documentation. Keycloak's full current
MCP conformance remains experimental because Resource Indicators and Client ID
Metadata Documents require experimental features. This setup uses the documented
stable scope/audience mapping and pre-registration instead. Don't describe it as
verified full MCP conformance. A client requiring dynamic registration or Client
ID Metadata Documents needs separate operator configuration and acceptance proof.
No issuer version has been tested live by this change.

Sources: [MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization),
[SDK resource-server setup](https://py.sdk.modelcontextprotocol.io/run/authorization/),
[Keycloak MCP setup and limitations](https://www.keycloak.org/securing-apps/mcp-authz-server),
[Keycloak 26.7.5 administration](https://www.keycloak.org/docs/26.7.5/server_admin/index.html).

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
3. Register the MCP client as a public native client with Standard Flow enabled,
   PKCE S256 required, and its exact approved callback URI. Disable implicit and
   password grants. Attach the two optional client scopes. Preload the registered
   client ID in a client that supports pre-registration; the SDK's OAuth token
   storage supports stored client information. The resource client's secret
   must never be installed in the public MCP client.
4. Confirm the realm's OpenID Connect discovery document reports the exact issuer
   and its authorization/token endpoints. Configure short-lived access tokens,
   consent, user access, refresh policy, and revocation in the issuer. The
   introspection response must include `active`, `iss`, `sub`, `client_id`, `aud`,
   integer `exp`, string `scope`, and `token_type=Bearer`. An optional `nbf` must
   already have passed. ID tokens aren't accepted as API credentials.
5. Set the PolicyNIM environment settings below and perform the live acceptance
   checks before directing users to OAuth.

```bash
POLICYNIM_MCP_REQUIRE_AUTH=true
POLICYNIM_MCP_PUBLIC_BASE_URL=https://policy.example
POLICYNIM_MCP_OAUTH_ISSUER_URL=https://identity.example/realms/policynim
POLICYNIM_MCP_OAUTH_CLIENT_ID=https://policy.example/mcp
POLICYNIM_MCP_OAUTH_TIMEOUT_SECONDS=5
POLICYNIM_MCP_OAUTH_ALLOW_LEGACY_KEYS=false
```

Set `POLICYNIM_MCP_OAUTH_CLIENT_SECRET` through the secret manager. Don't put it
in a client command, committed file, URL, or support output. Issuer/client
settings are absent by default. Supplying a partial configuration fails startup.
Issuer URLs require HTTPS without credentials, query, fragment, or trailing
slash. Public resource hosting also requires HTTPS. Existing settings precedence
is preserved: direct constructor values, process environment, then dotenv/config
sources as defined by `Settings`.

The verifier posts to the fixed realm endpoint
`<issuer>/protocol/openid-connect/token/introspect` with `client_secret_post` form
authentication over HTTPS. HTTPX encodes the client ID and secret as form fields;
they aren't placed in the URL or forwarded to NVIDIA. It doesn't follow redirects, use environment proxies, accept
token-selected endpoints, or cache successful token validation. Timeout is
bounded between greater than zero and 30 seconds; the default is five seconds.
A fresh HTTP client closes after each introspection, including failure.

## Discovery and operation permissions

`GET /.well-known/oauth-protected-resource/mcp` publishes the exact `/mcp`
resource, external authorization server, header bearer method, and supported
scopes. It doesn't publish client secrets or provider configuration. A client
uses the issuer's own discovery/login/token endpoints to obtain its access token.

| Request | Required authority |
| --- | --- |
| MCP discovery, initialization, ping | Any currently valid access token |
| `policy_search` | `policies:read` |
| `policy_preflight` | `policies:read policies:evaluate` |
| `/healthz` | Public, sanitized readiness only |

Missing, expired, inactive, malformed, foreign-issuer or foreign-audience tokens
receive HTTP `401` with `WWW-Authenticate: Bearer` and `resource_metadata`.
Authenticated POST bodies are capped at 1 MiB before dispatch.
Insufficient tool scopes receive HTTP `403` with `error="insufficient_scope"`,
the complete required `scope` set, and the same metadata URL. Both `/mcp` and
`/mcp/` remain behind authorization. Host/Origin checks run before authentication.

The SDK's `validate_token_resource=True` is explicit. The verifier checks exact
issuer and audience membership before returning that resource to the SDK.
Each protected HTTP request is introspected again. Each tool invocation checks
its scopes and expiry before admission/provider allocation, and search/preflight
services check again before their pipelines. A frozen principal carries issuer,
subject, client ID, scopes, credential source and expiry into service work.
It contains no access token. Local CLI and stdio MCP operations remain trusted
local calls, even when hosted auth is configured. OAuth authentication and scope
denials retain sanitized `mcp.auth` events without credentials or request bodies.

The shared public corpus doesn't grant access to another user's evaluation jobs.
No hosted evaluation-job route is introduced here; unknown protected operations
are denied by the service permission function. Job ownership remains part of the
future job implementation, not an implicit benefit of corpus read scope.

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

In compatibility mode, beta key rotation revokes the previous key immediately;
operator revoke and account suspension block subsequent authentication. The
existing beta request counter still charges authenticated HTTP requests,
including discovery; a denied compatibility key receives `401` in OAuth mode.
Non-OAuth deployments retain their existing `403` suspension and `429` quota
responses. OAuth tokens don't yet have principal quotas. Accepted-operation
quotas, discovery/polling exemptions and concurrent accounting belong to
[issue #98](https://github.com/nnennandukwe/policyNIM/issues/98).

Portal logout clears the portal cookie; it doesn't revoke beta keys or issuer
tokens. Revoke beta keys through the existing operator command or rotate them in
the portal. Revoke issuer tokens/sessions through Keycloak and remove tokens from
the client store. Introspection observes issuer revocation once the issuer reports
`active=false`; its semantics must be verified live. Already admitted synchronous
provider work drains under the existing cancellation/cleanup contract. Revocation
affects subsequent requests and doesn't recall work already admitted.

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
