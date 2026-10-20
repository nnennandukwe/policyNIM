"""Offline resource-server proofs through the SDK and real HTTP transport."""

from __future__ import annotations

import asyncio
import socket
import time
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Thread
from unittest.mock import Mock

import httpx
import pytest
import uvicorn
from pydantic import ValidationError
from starlette.testclient import TestClient

from policynim.interfaces import mcp as mcp_module
from policynim.services.authorization import (
    SUPPORTED_SCOPES,
    AuthenticatedPrincipal,
    AuthorizationError,
    IntrospectionTokenVerifier,
    authorize_operation,
    principal_from_access_token,
)
from policynim.services.beta_auth import BetaAuthService
from policynim.services.preflight import PreflightService
from policynim.services.search import SearchService
from policynim.settings import Settings
from policynim.storage.auth_store import AuthStore
from policynim.types import PreflightRequest, SearchRequest, SearchResult

ISSUER = "https://identity.example/realms/policynim"
RESOURCE = "https://policy.example/mcp"
METADATA_PATH = "/.well-known/oauth-protected-resource/mcp"


def oauth_settings(tmp_path: Path, **overrides: object) -> Settings:
    """Keep every fixture's mutable state out of the checkout."""
    values: dict[str, object] = {
        "mcp_require_auth": True,
        "mcp_public_base_url": "https://policy.example",
        "mcp_oauth_issuer_url": ISSUER,
        "mcp_oauth_client_id": "policynim-resource",
        "mcp_oauth_client_secret": "fixture-introspection-secret",
        "index_db_path": tmp_path / "index.sqlite3",
        "beta_auth_db_path": tmp_path / "auth.sqlite3",
        "runtime_rules_artifact_path": tmp_path / "rules.json",
    }
    values.update(overrides)
    return Settings.model_validate(values)


def token_claims(**overrides: object) -> dict[str, object]:
    """Provide a valid control before changing exactly one token claim."""
    claims: dict[str, object] = {
        "active": True,
        "iss": ISSUER,
        "sub": "user-123",
        "client_id": "registered-pkce-client",
        "aud": ["other-service", RESOURCE],
        "exp": int(time.time()) + 300,
        "scope": " ".join(SUPPORTED_SCOPES),
        "token_type": "Bearer",
    }
    claims.update(overrides)
    return claims


def install_issuer(
    monkeypatch: pytest.MonkeyPatch, settings: Settings, claims: dict[str, object]
) -> list[httpx.Request]:
    """Inject only the external issuer's HTTP transport, preserving SDK validation."""
    requests: list[httpx.Request] = []

    def introspect(request: httpx.Request) -> httpx.Response:
        """Return mutable issuer state so revocation takes effect on the next request."""
        requests.append(request)
        return httpx.Response(200, json=claims)

    verifier = IntrospectionTokenVerifier(settings, transport=httpx.MockTransport(introspect))
    monkeypatch.setattr(mcp_module, "IntrospectionTokenVerifier", lambda *args, **kwargs: verifier)
    return requests


def rpc(
    client: TestClient,
    method: str,
    params: dict[str, object] | None = None,
    token: str | None = "issuer-access-token",
) -> httpx.Response:
    """Send an actual JSON-RPC request through the HTTP app and SDK dispatcher."""
    headers = {
        "Accept": "application/json, text/event-stream",
        "MCP-Protocol-Version": "2026-07-28",
    }
    headers["Mcp-Method"] = method
    if params and isinstance(params.get("name"), str):
        headers["Mcp-Name"] = str(params["name"])
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    return client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": {
                **(params or {}),
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        },
        headers=headers,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"active": False},
        {"active": "true"},
        {"iss": ISSUER + "/wrong"},
        {"aud": "another-resource"},
        {"aud": []},
        {"exp": 0},
        {"exp": True},
        {"exp": "9999999999"},
        {"nbf": 9999999999},
        {"sub": ""},
        {"client_id": ""},
        {"token_type": "ID"},
        {"scope": ["policies:read"]},
        {"aud": [True]},
    ],
)
def test_invalid_tokens_fail_before_provider_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, overrides: dict[str, object]
) -> None:
    """Use the valid control, isolate one fault, and recover through the same route."""
    settings = oauth_settings(tmp_path)
    claims = token_claims()
    requests = install_issuer(monkeypatch, settings, claims)
    forbidden = Mock(side_effect=AssertionError("Rejected auth allocated a provider"))
    monkeypatch.setattr(mcp_module, "create_search_service", forbidden)
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        assert rpc(client, "tools/list").status_code == 200
        claims.update(overrides)
        rejected = rpc(client, "tools/call", {"name": "policy_search", "arguments": {"query": "q"}})
        assert rejected.status_code == 401
        assert 'error="invalid_token"' in rejected.headers["WWW-Authenticate"]
        assert METADATA_PATH in rejected.headers["WWW-Authenticate"]
        claims.clear()
        claims.update(token_claims())
        assert rpc(client, "tools/list").status_code == 200
    assert len(requests) == 3
    forbidden.assert_not_called()


def test_discovery_scope_challenges_and_principal_propagation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Prove per-call scopes and immutable service identity beyond connection authentication."""
    settings = oauth_settings(tmp_path)
    claims = token_claims(scope="policies:read")
    requests = install_issuer(monkeypatch, settings, claims)
    principals: list[AuthenticatedPrincipal | None] = []

    class SearchFixture:
        """Capture only service authority; provider interfaces receive no bearer token."""

        def search(
            self, request: SearchRequest, *, principal: AuthenticatedPrincipal | None = None
        ) -> SearchResult:
            """Record the worker's principal and return a typed search response."""
            principals.append(principal)
            return SearchResult(query=request.query, top_k=request.top_k, hits=[])

        def close(self) -> None:
            """The fixture owns no network resources."""

    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: SearchFixture())
    denied = Mock(side_effect=AssertionError("Insufficient scope allocated preflight providers"))
    monkeypatch.setattr(mcp_module, "create_preflight_service", denied)
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        discovery = client.get(METADATA_PATH)
        assert discovery.status_code == 200
        assert discovery.json()["resource"] == RESOURCE
        assert discovery.json()["authorization_servers"] == [ISSUER]
        assert discovery.json()["scopes_supported"] == SUPPORTED_SCOPES
        assert requests == []
        challenge = rpc(client, "tools/list", token=None)
        assert challenge.status_code == 401
        assert METADATA_PATH in challenge.headers["WWW-Authenticate"]
        assert rpc(client, "tools/list").status_code == 200
        allowed = rpc(client, "tools/call", {"name": "policy_search", "arguments": {"query": "q"}})
        assert allowed.status_code == 200
        assert not allowed.json()["result"]["isError"]
        restricted = rpc(
            client, "tools/call", {"name": "policy_preflight", "arguments": {"task": "q"}}
        )
        assert restricted.status_code == 403
        assert 'error="insufficient_scope"' in restricted.headers["WWW-Authenticate"]
        assert 'scope="policies:evaluate policies:read"' in restricted.headers["WWW-Authenticate"]
        assert METADATA_PATH in restricted.headers["WWW-Authenticate"]
        assert rpc(client, "tools/list", token=None).status_code == 401
        assert client.get("/healthz").status_code == 503  # no index; no auth requirement
    denied.assert_not_called()
    assert len(principals) == 1
    principal = principals[0]
    assert principal is not None
    assert (principal.issuer, principal.subject, principal.client_id) == (
        ISSUER,
        "user-123",
        "registered-pkce-client",
    )
    assert principal.scopes == frozenset({"policies:read"})
    assert "issuer-access-token" not in repr(principal)
    with pytest.raises(FrozenInstanceError):
        setattr(principal, "subject", "another-user")
    assert all(
        str(request.url) == ISSUER + "/protocol/openid-connect/token/introspect"
        for request in requests
    )
    assert all("Authorization" not in request.headers for request in requests)


@pytest.mark.parametrize("fault", ["inactive", "unavailable", "redirect", "malformed"])
def test_introspection_revocation_failure_and_recovery(tmp_path: Path, fault: str) -> None:
    """Never cache successful introspection or follow credential-bearing redirects."""
    state = {"fault": ""}
    calls = 0

    def introspect(request: httpx.Request) -> httpx.Response:
        """Switch the same issuer endpoint between successful and failed responses."""
        nonlocal calls
        calls += 1
        if state["fault"] == "unavailable":
            raise httpx.ConnectError("fixture failure", request=request)
        if state["fault"] == "redirect":
            return httpx.Response(302, headers={"Location": "https://foreign.example"})
        if state["fault"] == "malformed":
            return httpx.Response(200, content=b"not json")
        return httpx.Response(200, json=token_claims(active=state["fault"] != "inactive"))

    verifier = IntrospectionTokenVerifier(
        oauth_settings(tmp_path), transport=httpx.MockTransport(introspect)
    )

    async def exercise() -> None:
        """Check the same token again after revocation or issuer recovery."""
        assert await verifier.verify_token("access-token") is not None
        state["fault"] = fault
        assert await verifier.verify_token("access-token") is None
        state["fault"] = ""
        assert await verifier.verify_token("access-token") is not None

    asyncio.run(exercise())
    assert calls == 3


def test_legacy_key_migration_rotation_revocation_and_recovery(tmp_path: Path) -> None:
    """Keep old keys opt-in, identify their owners, and reject revoked credentials."""
    settings = oauth_settings(
        tmp_path, mcp_oauth_allow_legacy_keys=True, mcp_bearer_tokens=["operator-fixture"]
    )
    store = AuthStore(path=settings.beta_auth_db_path)
    service = BetaAuthService(store=store, settings=settings)
    account = store.upsert_account_from_github(
        github_user_id=42, github_login="fixture-user", email=None, now=datetime.now(UTC)
    )
    issued = service.issue_api_key(account_id=account.account_id)
    external_tokens: list[str] = []

    def external_calls(request: httpx.Request) -> httpx.Response:
        """Reject legacy keys that aren't valid at the external issuer."""
        from urllib.parse import parse_qs

        external_tokens.append(parse_qs(request.content.decode())["token"][0])
        return httpx.Response(200, json={"active": False})

    verifier = IntrospectionTokenVerifier(
        settings, beta_auth_service=service, transport=httpx.MockTransport(external_calls)
    )

    async def exercise() -> None:
        """Migrate, rotate, revoke, recover, suspend and disable compatibility."""
        token = await verifier.verify_token(issued.api_key)
        assert token is not None
        principal = principal_from_access_token(token)
        assert principal.source == "beta_key"
        assert principal.subject == str(account.account_id)
        rotated = service.issue_api_key(account_id=account.account_id)
        assert await verifier.verify_token(issued.api_key) is None
        assert await verifier.verify_token(rotated.api_key) is not None
        service.revoke_api_key(github_login=account.github_login)
        assert await verifier.verify_token(rotated.api_key) is None
        recovered = service.issue_api_key(account_id=account.account_id)
        assert await verifier.verify_token(recovered.api_key) is not None
        service.suspend_account(github_login=account.github_login)
        assert await verifier.verify_token(recovered.api_key) is None
        service.resume_account(github_login=account.github_login)
        assert await verifier.verify_token(recovered.api_key) is not None
        assert await verifier.verify_token("operator-fixture") is not None
        disabled = IntrospectionTokenVerifier(
            oauth_settings(tmp_path, mcp_bearer_tokens=["operator-fixture"]),
            beta_auth_service=service,
            transport=httpx.MockTransport(external_calls),
        )
        assert await disabled.verify_token(recovered.api_key) is None
        assert await disabled.verify_token("operator-fixture") is None

    asyncio.run(exercise())
    assert issued.api_key not in external_tokens
    assert len(external_tokens) == 2  # only OAuth-only attempts consult the issuer


@pytest.mark.parametrize(
    "overrides,setting",
    [
        ({"mcp_oauth_issuer_url": "http://identity.example/realm"}, "ISSUER_URL"),
        ({"mcp_oauth_issuer_url": ISSUER + "/"}, "ISSUER_URL"),
        ({"mcp_oauth_issuer_url": ISSUER + "?a=b"}, "ISSUER_URL"),
        ({"mcp_oauth_issuer_url": "https://name:password@identity.example/realm"}, "ISSUER_URL"),
        ({"mcp_oauth_client_secret": ""}, "CLIENT_SECRET"),
        ({"mcp_oauth_client_id": ""}, "CLIENT_ID"),
        ({"mcp_require_auth": False}, "REQUIRE_AUTH"),
        ({"mcp_public_base_url": "http://policy.example"}, "PUBLIC_BASE_URL"),
        ({"mcp_oauth_issuer_url": None}, "ISSUER_URL"),
        ({"mcp_oauth_timeout_seconds": 0}, "mcp_oauth_timeout_seconds"),
        ({"mcp_oauth_max_concurrent_requests": 0}, "mcp_oauth_max_concurrent_requests"),
        ({"mcp_oauth_max_concurrent_requests": 257}, "mcp_oauth_max_concurrent_requests"),
        ({"mcp_oauth_request_body_timeout_seconds": 0}, "mcp_oauth_request_body_timeout_seconds"),
        ({"mcp_oauth_request_body_timeout_seconds": 301}, "mcp_oauth_request_body_timeout_seconds"),
    ],
)
def test_oauth_settings_reject_unsafe_or_partial_configuration(
    tmp_path: Path, overrides: dict[str, object], setting: str
) -> None:
    """Expose actionable config names without exposing the introspection secret."""
    with pytest.raises(ValidationError, match=setting) as caught:
        oauth_settings(tmp_path, **overrides)
    assert "fixture-introspection-secret" not in str(caught.value)


def test_oauth_settings_environment_parity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Load the documented environment names with the same validated direct settings."""
    for name, value in {
        "POLICYNIM_MCP_REQUIRE_AUTH": "true",
        "POLICYNIM_MCP_PUBLIC_BASE_URL": "https://policy.example",
        "POLICYNIM_MCP_OAUTH_ISSUER_URL": ISSUER,
        "POLICYNIM_MCP_OAUTH_CLIENT_ID": "policynim-resource",
        "POLICYNIM_MCP_OAUTH_CLIENT_SECRET": "fixture-introspection-secret",
        "POLICYNIM_MCP_OAUTH_ALLOW_LEGACY_KEYS": "false",
        "POLICYNIM_MCP_OAUTH_TIMEOUT_SECONDS": "5",
        "POLICYNIM_MCP_OAUTH_MAX_CONCURRENT_REQUESTS": "64",
        "POLICYNIM_MCP_OAUTH_REQUEST_BODY_TIMEOUT_SECONDS": "30",
    }.items():
        monkeypatch.setenv(name, value)
    loaded = Settings()
    direct = oauth_settings(tmp_path)
    for field in [
        "mcp_oauth_issuer_url",
        "mcp_oauth_client_id",
        "mcp_oauth_client_secret",
        "mcp_oauth_allow_legacy_keys",
        "mcp_oauth_timeout_seconds",
        "mcp_oauth_max_concurrent_requests",
        "mcp_oauth_request_body_timeout_seconds",
    ]:
        assert getattr(loaded, field) == getattr(direct, field)
    assert "fixture-introspection-secret" not in repr(loaded)


def test_service_permission_guards_precede_all_dependencies() -> None:
    """Exercise real service methods with a dependency that fails if reached."""
    from test_preflight_service import (
        MockEmbedder as PreflightEmbedder,
    )
    from test_preflight_service import (
        MockGenerator,
        MockPolicyCompiler,
        MockReranker,
    )
    from test_preflight_service import (
        MockIndexStore as PreflightIndexStore,
    )
    from test_preflight_service import (
        make_chunk as preflight_chunk,
    )
    from test_search_service import MockEmbedder, MockIndexStore, make_chunk

    from policynim.types import GeneratedCompiledPolicyDraft, GeneratedPreflightDraft

    principal = AuthenticatedPrincipal(
        issuer=ISSUER, subject="user", client_id="client", scopes=frozenset(), source="oauth"
    )
    search_store = MockIndexStore([make_chunk(chunk_id="BACKEND-1", domain="backend")])
    search = SearchService(embedder=MockEmbedder(), index_store=search_store)
    permitted = replace(principal, scopes=frozenset(SUPPORTED_SCOPES))
    assert search.search(SearchRequest(query="backend logs"), principal=permitted).hits

    with pytest.raises(AuthorizationError, match="insufficient_scope"):
        search.search(SearchRequest(query="must not embed"), principal=principal)
    assert search_store.last_query_embedding == [1.0, 0.0]
    compiler = MockPolicyCompiler(GeneratedCompiledPolicyDraft(insufficient_context=True))
    generator = MockGenerator(GeneratedPreflightDraft(summary="unused"))
    preflight = PreflightService(
        generator=generator,
        compiler=compiler,
        embedder=PreflightEmbedder(),
        index_store=PreflightIndexStore(
            [
                preflight_chunk(
                    chunk_id="BACKEND-1",
                    policy_id="BACKEND-LOG-001",
                    domain="backend",
                    score=0.99,
                )
            ]
        ),
        reranker=MockReranker(order=["BACKEND-1"]),
    )
    assert preflight.preflight(
        PreflightRequest(task="backend guidance"), principal=permitted
    ).insufficient_context
    assert compiler.calls == 1

    with pytest.raises(AuthorizationError, match="insufficient_scope"):
        preflight.preflight(PreflightRequest(task="must not compile"), principal=principal)
    with pytest.raises(AuthorizationError, match="insufficient_scope"):
        preflight.preflight_with_trace(
            PreflightRequest(task="must not compile"), principal=principal
        )
    assert compiler.calls == 1
    assert generator.last_request is None
    authorize_operation(permitted, "policy_preflight")
    with pytest.raises(AuthorizationError, match="invalid_token"):
        authorize_operation(replace(permitted, expires_at=0), "policy_search")
    with pytest.raises(AuthorizationError, match="Unknown protected operation"):
        authorize_operation(permitted, "private-evaluation-job")


def test_real_http_client_authenticates_every_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross a TCP socket and real SDK transport with a deterministic external issuer double."""
    settings = oauth_settings(tmp_path)
    claims = token_claims()
    install_issuer(monkeypatch, settings, claims)
    principals: list[AuthenticatedPrincipal | None] = []

    class SearchFixture:
        """Provide a deterministic service behind the real HTTP listener."""

        def search(
            self, request: SearchRequest, *, principal: AuthenticatedPrincipal | None = None
        ) -> SearchResult:
            """Record the worker authority without contacting a model provider."""
            principals.append(principal)
            return SearchResult(query=request.query, top_k=request.top_k, hits=[])

        def close(self) -> None:
            """The fixture owns no resources."""

    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: SearchFixture())
    app = mcp_module._build_streamable_http_app(settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            if not thread.is_alive() or time.monotonic() >= deadline:
                pytest.fail("Local HTTP server failed to start")
            time.sleep(0.01)
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=5) as client:
            assert client.get(METADATA_PATH).json()["resource"] == RESOURCE
            headers = {
                "Accept": "application/json, text/event-stream",
                "MCP-Protocol-Version": "2026-07-28",
                "Mcp-Method": "tools/list",
            }
            message = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": {},
                    }
                },
            }
            assert client.post("/mcp", json=message, headers=headers).status_code == 401
            headers["Authorization"] = "Bearer external-fixture-token"
            accepted = client.post("/mcp", json=message, headers=headers)
            assert accepted.status_code == 200
            assert len(accepted.json()["result"]["tools"]) == 4
            headers["Mcp-Method"] = "tools/call"
            headers["Mcp-Name"] = "policy_search"
            call = {
                **message,
                "method": "tools/call",
                "params": {
                    **message["params"],
                    "name": "policy_search",
                    "arguments": {"query": "policy"},
                },
            }
            allowed = client.post("/mcp", json=call, headers=headers)
            assert allowed.status_code == 200
            assert not allowed.json()["result"]["isError"]
            assert len(principals) == 1
            assert principals[0] is not None and principals[0].subject == "user-123"
            headers["Mcp-Method"] = "tools/list"
            headers.pop("Mcp-Name")
            claims["active"] = False
            assert client.post("/mcp", json=message, headers=headers).status_code == 401
            claims["active"] = True
            assert client.post("/mcp", json=message, headers=headers).status_code == 200
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()


@pytest.mark.parametrize("path", ["/mcp", "/mcp/"])
def test_oauth_slash_host_and_origin_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Keep slash requests protected and reject untrusted authorities before introspection."""
    settings = oauth_settings(tmp_path)
    requests = install_issuer(monkeypatch, settings, token_claims())
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        missing = client.get(path, follow_redirects=False)
        assert missing.status_code == 401
        assert METADATA_PATH in missing.headers["WWW-Authenticate"]
        for headers, status in [
            ({"Host": "foreign.example"}, 421),
            ({"Origin": "https://foreign.example"}, 403),
        ]:
            rejected = client.post(
                path,
                headers={"Authorization": "Bearer fixture", **headers},
                json={},
                follow_redirects=False,
            )
            assert rejected.status_code == status
        assert requests == []


def test_oauth_portal_requires_explicit_key_compatibility(tmp_path: Path) -> None:
    """Prevent the portal from issuing keys that OAuth-only deployments won't accept."""
    with pytest.raises(ValidationError, match="Disable POLICYNIM_BETA_SIGNUP_ENABLED"):
        oauth_settings(
            tmp_path,
            beta_signup_enabled=True,
            beta_session_secret="session-fixture",
            beta_github_client_id="portal-fixture",
            beta_github_client_secret="secret",
        )


def test_oauth_request_size_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject an oversized authenticated request before SDK dispatch or provider work."""
    from policynim.interfaces.mcp_authorization import MAX_AUTH_BODY_BYTES

    settings = oauth_settings(tmp_path)
    install_issuer(monkeypatch, settings, token_claims())
    forbidden = Mock(side_effect=AssertionError("Oversized request reached a provider"))
    monkeypatch.setattr(mcp_module, "create_search_service", forbidden)
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        response = client.post(
            "/mcp",
            content=b"x" * (MAX_AUTH_BODY_BYTES + 1),
            headers={"Authorization": "Bearer fixture"},
        )
        assert response.status_code == 413
        assert rpc(client, "tools/list").status_code == 200
    forbidden.assert_not_called()


@pytest.mark.parametrize("token", ["pnm_valid-opaque-issuer-token", "opaque-token"])
def test_issuer_tokens_arent_classified_by_local_key_prefix(tmp_path: Path, token: str) -> None:
    """Opaque issuer tokens can share a prefix with locally issued API keys."""
    verifier = IntrospectionTokenVerifier(
        oauth_settings(tmp_path),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=token_claims())),
    )
    accepted = asyncio.run(verifier.verify_token(token))
    assert accepted is not None
    assert principal_from_access_token(accepted).source == "oauth"


@pytest.mark.parametrize("mode", ["auto", "legacy"])
def test_oauth_sdk_clients_cross_real_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    """Run both supported SDK protocol eras through external-token authorization."""
    import httpx2
    from fixtures.mcp_fixture_server import offline_services
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client
    from test_mcp_protocol import _exercise_client, _serve_http

    settings = oauth_settings(tmp_path, default_top_k=3)
    requests = install_issuer(monkeypatch, settings, token_claims())

    async def exercise() -> None:
        """Preserve normal tools, errors and cleanup under OAuth authentication."""
        with offline_services(settings) as events:
            app = mcp_module._build_streamable_http_app(settings)
            async with _serve_http(app) as origin:
                async with httpx2.AsyncClient(
                    headers={"Authorization": "Bearer issuer-token"}, timeout=5, trust_env=False
                ) as http_client:
                    async with Client(
                        streamable_http_client(origin + "/mcp", http_client=http_client),
                        mode=mode,
                        read_timeout_seconds=5,
                    ) as client:
                        await _exercise_client(client, mode)
            assert events.count("search.created") == events.count("search.closed") == 4
            assert events.count("preflight.created") == events.count("preflight.closed") == 2

    asyncio.run(exercise())
    assert len(requests) >= 8


def test_introspection_form_auth_preserves_resource_client_credentials(tmp_path: Path) -> None:
    """URL-shaped IDs and reserved secret characters retain their configured values."""
    from urllib.parse import parse_qs

    secret = "fixture:secret &space"
    settings = oauth_settings(
        tmp_path, mcp_oauth_client_id=RESOURCE, mcp_oauth_client_secret=secret
    )
    requests: list[httpx.Request] = []

    def introspect(request: httpx.Request) -> httpx.Response:
        """Parse credentials as the selected issuer's form-based authenticator does."""
        requests.append(request)
        form = parse_qs(request.content.decode())
        assert form["client_id"] == [RESOURCE]
        assert form["client_secret"] == [secret]
        assert form["token"] == ["fixture"]
        assert form["token_type_hint"] == ["access_token"]
        assert "Authorization" not in request.headers
        assert request.url.query == b""
        assert secret not in str(request.url)
        return httpx.Response(200, json=token_claims())

    verifier = IntrospectionTokenVerifier(settings, transport=httpx.MockTransport(introspect))
    assert asyncio.run(verifier.verify_token("fixture")) is not None
    assert len(requests) == 1


def test_concurrent_http_requests_keep_principals_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two active callers retain their own identities in synchronous service workers."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from urllib.parse import parse_qs

    settings = oauth_settings(tmp_path)
    barrier = Barrier(2)
    seen: list[tuple[str, str]] = []

    def introspect(request: httpx.Request) -> httpx.Response:
        """Bind each deterministic issuer token to its own subject."""
        subject = parse_qs(request.content.decode())["token"][0]
        return httpx.Response(200, json=token_claims(sub=subject))

    verifier = IntrospectionTokenVerifier(settings, transport=httpx.MockTransport(introspect))
    monkeypatch.setattr(mcp_module, "IntrospectionTokenVerifier", lambda *args, **kwargs: verifier)

    class ConcurrentSearch:
        """Synchronize two workers before inspecting their service principals."""

        def search(
            self, request: SearchRequest, *, principal: AuthenticatedPrincipal | None = None
        ) -> SearchResult:
            """Check the principal after another user's request has entered service work."""
            barrier.wait(timeout=5)
            assert principal is not None
            seen.append((request.query, principal.subject))
            return SearchResult(query=request.query, top_k=request.top_k, hits=[])

        def close(self) -> None:
            """The fixture owns no resources."""

    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: ConcurrentSearch())
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        with ThreadPoolExecutor(max_workers=2) as executor:
            pending = [
                executor.submit(
                    rpc,
                    client,
                    "tools/call",
                    {"name": "policy_search", "arguments": {"query": user}},
                    user,
                )
                for user in ["alice", "bob"]
            ]
            for result in pending:
                response = result.result(timeout=10)
                assert response.status_code == 200
                assert not response.json()["result"]["isError"]
    assert sorted(seen) == [("alice", "alice"), ("bob", "bob")]


def test_introspection_diagnostics_dont_log_credentials(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Give operators a failure class while keeping tokens and upstream details private."""

    def fail(request: httpx.Request) -> httpx.Response:
        """Simulate an HTTP exception containing private upstream detail."""
        raise httpx.ConnectError("private-token-and-secret", request=request)

    verifier = IntrospectionTokenVerifier(
        oauth_settings(tmp_path), transport=httpx.MockTransport(fail)
    )
    assert asyncio.run(verifier.verify_token("private-access-token")) is None
    assert "issuer_unavailable" in caplog.text
    assert "private-token-and-secret" not in caplog.text
    assert "private-access-token" not in caplog.text
    assert "fixture-introspection-secret" not in caplog.text


def test_oauth_denials_keep_sanitized_auth_events(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the existing auth audit event for both SDK and operation denials."""
    settings = oauth_settings(tmp_path)
    install_issuer(monkeypatch, settings, token_claims(scope="policies:read"))
    events: list[dict[str, object]] = []

    def record(event: str, **fields: object) -> None:
        """Capture the exact sanitized fields passed to the hosted event emitter."""
        events.append({"event": event, **fields})

    monkeypatch.setattr(mcp_module, "_emit_hosted_event", record)
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        assert rpc(client, "tools/list", token=None).status_code == 401
        assert rpc(client, "tools/list").status_code == 200
        denied = rpc(
            client,
            "tools/call",
            {"name": "policy_preflight", "arguments": {"task": "private-task-input"}},
        )
        assert denied.status_code == 403
    assert [event["auth_result"] for event in events] == ["unauthorized", "insufficient_scope"]
    assert all(event["event"] == "mcp.auth" for event in events)
    assert all(event["request_id"] is None and event["tool_name"] is None for event in events)
    assert "private-task-input" not in str(events)
    assert "issuer-access-token" not in str(events)
    assert "fixture-introspection-secret" not in str(events)


@pytest.mark.parametrize("oauth", [False, True])
def test_local_stdio_works_with_hosted_auth_settings(tmp_path: Path, oauth: bool) -> None:
    """A real local subprocess remains usable with either hosted credential configuration."""
    import os
    import sys
    import textwrap

    from mcp import Client
    from mcp.client.stdio import StdioServerParameters

    root = Path(__file__).resolve().parents[1]
    script = textwrap.dedent(f"""
        from fixtures.mcp_fixture_server import OfflineSettings, offline_services
        from policynim.interfaces import mcp
        settings = OfflineSettings(
            default_top_k=3, mcp_require_auth=True, mcp_bearer_tokens=['local-fixture'],
            mcp_public_base_url='https://policy.example',
            mcp_oauth_issuer_url={repr(ISSUER) if oauth else "None"},
            mcp_oauth_client_id={'"resource-client"' if oauth else "None"},
            mcp_oauth_client_secret={'"fixture-secret"' if oauth else "None"},
        )
        with offline_services(settings):
            mcp.run_server('stdio')
    """)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", script],
        cwd=tmp_path,
        env={"PYTHONPATH": os.pathsep.join([str(root / "src"), str(root / "tests")])},
    )

    async def exercise() -> None:
        """Call a tool over stdio without creating an HTTP auth context or using an issuer."""
        async with asyncio.timeout(15):
            async with Client(params, read_timeout_seconds=5) as client:
                result = await client.call_tool("policy_search", {"query": "local"})
                assert result.is_error is False
                assert result.structured_content is not None
                assert result.structured_content["query"] == "local"

    asyncio.run(exercise())


def test_oauth_only_startup_doesnt_require_beta_storage(tmp_path: Path) -> None:
    """An unavailable beta volume doesn't block an issuer-only resource server."""
    from policynim.errors import ConfigurationError

    blocked = tmp_path / "unavailable-beta-volume"
    blocked.write_text("This fixture is a file, so a database can't be created underneath it.")
    settings = oauth_settings(tmp_path, beta_auth_db_path=blocked / "auth.sqlite3")
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        assert client.get(METADATA_PATH).status_code == 200
        assert rpc(client, "tools/list", token=None).status_code == 401
    assert blocked.read_text().startswith("This fixture is a file")
    with pytest.raises(ConfigurationError, match="Hosted beta auth initialization failed"):
        mcp_module._build_streamable_http_app(
            oauth_settings(
                tmp_path,
                beta_auth_db_path=blocked / "auth.sqlite3",
                mcp_oauth_allow_legacy_keys=True,
            )
        )


def test_oauth_legacy_mode_initializes_needed_beta_storage(tmp_path: Path) -> None:
    """Legacy compatibility can still read persisted keys after portal signup is disabled."""
    settings = oauth_settings(tmp_path, mcp_oauth_allow_legacy_keys=True)
    assert not settings.beta_auth_db_path.exists()
    mcp_module._build_streamable_http_app(settings)
    assert settings.beta_auth_db_path.is_file()


def test_oauth_only_mode_doesnt_deny_an_issuer_token_in_an_old_key_list(tmp_path: Path) -> None:
    """A stale compatibility list can't override authority verified by the external issuer."""
    token = "opaque-issuer-token"
    verifier = IntrospectionTokenVerifier(
        oauth_settings(tmp_path, mcp_bearer_tokens=[token]),
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=token_claims())),
    )
    accepted = asyncio.run(verifier.verify_token(token))
    assert accepted is not None
    assert principal_from_access_token(accepted).source == "oauth"


@pytest.mark.parametrize("expire_at_check", [2, 3])
def test_dispatch_expiry_returns_http_challenge_before_provider_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, expire_at_check: int
) -> None:
    """Expiry at handler or worker admission keeps HTTP refresh challenges and audit events."""
    from types import SimpleNamespace

    from policynim.services import authorization as auth_module

    settings = oauth_settings(tmp_path)
    install_issuer(monkeypatch, settings, token_claims())
    clock = {"checks": 0, "expire_at": 999}
    events: list[dict[str, object]] = []
    factories: list[object] = []
    now = time.time()

    def current_time() -> float:
        """Expire after introspection, leaving the SDK's own wall clock unchanged."""
        clock["checks"] += 1
        return now + 1000 if clock["checks"] >= clock["expire_at"] else now

    def record(event: str, **fields: object) -> None:
        """Capture audit events without their logger transport."""
        events.append({"event": event, **fields})

    class SearchFixture:
        """Return a successful control without allocating model clients."""

        def search(
            self, request: SearchRequest, *, principal: AuthenticatedPrincipal | None = None
        ) -> SearchResult:
            """Keep the same service signature used by real search calls."""
            return SearchResult(query=request.query, top_k=request.top_k, hits=[])

        def close(self) -> None:
            """The fixture owns no resources."""

    def create_service(settings: Settings) -> SearchFixture:
        """Track provider allocation before changing only the expiry condition."""
        factories.append(object())
        return SearchFixture()

    monkeypatch.setattr(auth_module, "time", SimpleNamespace(time=current_time))
    monkeypatch.setattr(mcp_module, "_emit_hosted_event", record)
    monkeypatch.setattr(mcp_module, "create_search_service", create_service)
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        params = {"name": "policy_search", "arguments": {"query": "q"}}
        control = rpc(client, "tools/call", params)
        assert control.status_code == 200
        assert not control.json()["result"]["isError"]
        assert len(factories) == 1
        clock.update(checks=0, expire_at=expire_at_check)
        rejected = rpc(client, "tools/call", params)
        assert rejected.status_code == 401
        assert rejected.json() == {"error": "invalid_token"}
        assert 'error="invalid_token"' in rejected.headers["WWW-Authenticate"]
        assert METADATA_PATH in rejected.headers["WWW-Authenticate"]
        assert len(factories) == 1
        auth_events = [event for event in events if event["event"] == "mcp.auth"]
        assert [event["auth_result"] for event in auth_events] == ["unauthorized"]
        clock.update(checks=0, expire_at=999)
        assert rpc(client, "tools/call", params).status_code == 200
        assert len(factories) == 2


@pytest.mark.parametrize("limited_path", ["/mcp", METADATA_PATH, "/healthz"])
def test_oauth_authentication_capacity_precedes_issuer_calls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limited_path: str
) -> None:
    """Bound pre-authentication connections while public unauthenticated probes stay responsive."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    settings = oauth_settings(tmp_path, mcp_oauth_max_concurrent_requests=1)
    started = Event()
    release = Event()
    requests: list[httpx.Request] = []

    async def introspect(request: httpx.Request) -> httpx.Response:
        """Hold the sole admitted issuer connection until the test releases it."""
        requests.append(request)
        started.set()
        while not release.is_set():
            await asyncio.sleep(0.001)
        return httpx.Response(200, json=token_claims())

    verifier = IntrospectionTokenVerifier(settings, transport=httpx.MockTransport(introspect))
    monkeypatch.setattr(mcp_module, "IntrospectionTokenVerifier", lambda *args, **kwargs: verifier)
    with TestClient(
        mcp_module._build_streamable_http_app(settings), base_url="https://policy.example"
    ) as client:
        with ThreadPoolExecutor(max_workers=1) as executor:
            admitted = executor.submit(rpc, client, "tools/list")
            try:
                assert started.wait(5)
                excess = client.get(limited_path, headers={"Authorization": "Bearer invalid"})
                assert excess.status_code == 429
                assert excess.headers["Retry-After"] == "1"
                assert excess.json()["error"] == "auth_busy"
                assert "WWW-Authenticate" not in excess.headers
                assert len(requests) == 1
                assert client.get(METADATA_PATH).status_code == 200
                assert client.get("/healthz").status_code == 503
                assert len(requests) == 1
            finally:
                release.set()
            assert admitted.result(timeout=10).status_code == 200
        assert rpc(client, "tools/list").status_code == 200
        assert len(requests) == 2


def test_introspection_total_deadline_and_recovery(tmp_path: Path) -> None:
    """A slow issuer can't retain a connection beyond the total configured deadline."""
    settings = oauth_settings(tmp_path, mcp_oauth_timeout_seconds=0.01)
    slow = True

    async def introspect(request: httpx.Request) -> httpx.Response:
        """Delay only the first attempt, independently of socket read timeout behavior."""
        if slow:
            await asyncio.sleep(1)
        return httpx.Response(200, json=token_claims())

    verifier = IntrospectionTokenVerifier(settings, transport=httpx.MockTransport(introspect))

    async def exercise() -> None:
        """Reject the slow attempt and accept a later healthy response from the same issuer."""
        nonlocal slow
        assert await asyncio.wait_for(verifier.verify_token("fixture"), timeout=0.5) is None
        slow = False
        assert await verifier.verify_token("fixture") is not None

    asyncio.run(exercise())


@pytest.mark.parametrize("rejection", ["suspended", "quota", "revoked"])
def test_known_local_key_denials_never_reach_the_issuer(tmp_path: Path, rejection: str) -> None:
    """Distinguish a rejected issued key from an unrecognized opaque issuer token."""
    settings = oauth_settings(
        tmp_path,
        mcp_oauth_allow_legacy_keys=True,
        beta_daily_request_quota=1 if rejection == "quota" else 500,
    )
    store = AuthStore(path=settings.beta_auth_db_path)
    service = BetaAuthService(store=store, settings=settings)
    account = store.upsert_account_from_github(
        github_user_id=42, github_login="fixture-user", email=None, now=datetime.now(UTC)
    )
    key = service.issue_api_key(account_id=account.account_id).api_key
    issuer = Mock(side_effect=AssertionError("Known local secret reached the issuer"))
    verifier = IntrospectionTokenVerifier(
        settings, beta_auth_service=service, transport=httpx.MockTransport(issuer)
    )

    async def exercise() -> None:
        """Accept the active key, change only its local authorization, then reject it."""
        assert await verifier.verify_token(key) is not None
        if rejection == "suspended":
            service.suspend_account(github_login=account.github_login)
        elif rejection == "revoked":
            service.revoke_api_key(github_login=account.github_login)
        assert await verifier.verify_token(key) is None

    asyncio.run(exercise())
    issuer.assert_not_called()


def test_opaque_issuer_token_survives_missing_beta_database(tmp_path: Path) -> None:
    """A failed local lookup neither blocks issuer verification nor creates an empty database."""
    settings = oauth_settings(tmp_path, mcp_oauth_allow_legacy_keys=True)
    store = AuthStore(path=settings.beta_auth_db_path)
    service = BetaAuthService(store=store, settings=settings)
    verifier = IntrospectionTokenVerifier(
        settings,
        beta_auth_service=service,
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json=token_claims())),
    )
    assert asyncio.run(verifier.verify_token("pnm_opaque-issuer-token")) is not None
    backup = tmp_path / "auth-preserved.sqlite3"
    settings.beta_auth_db_path.replace(backup)
    accepted = asyncio.run(verifier.verify_token("pnm_opaque-issuer-token"))
    assert accepted is not None
    assert principal_from_access_token(accepted).source == "oauth"
    assert backup.is_file()
    assert not settings.beta_auth_db_path.exists()


def test_partial_http_body_times_out_and_releases_auth_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A real TCP client can't retain an authentication slot by withholding the body."""
    from urllib.parse import urlsplit

    from test_mcp_protocol import _serve_http

    settings = oauth_settings(
        tmp_path, mcp_oauth_max_concurrent_requests=1, mcp_oauth_request_body_timeout_seconds=0.02
    )
    install_issuer(monkeypatch, settings, token_claims())
    app = mcp_module._build_streamable_http_app(settings)
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {
            "_meta": {
                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                "io.modelcontextprotocol/clientCapabilities": {},
            }
        },
    }

    async def exercise() -> None:
        """Send a complete control, withhold one body, then verify a later request succeeds."""
        async with _serve_http(app) as origin:
            headers = {
                "Authorization": "Bearer fixture",
                "Mcp-Method": "tools/list",
                "MCP-Protocol-Version": "2026-07-28",
                "Accept": "application/json, text/event-stream",
            }
            async with httpx.AsyncClient(base_url=origin, timeout=3, trust_env=False) as client:
                assert (await client.post("/mcp", json=message, headers=headers)).status_code == 200
                target = urlsplit(origin)
                reader, writer = await asyncio.open_connection(target.hostname, target.port)
                try:
                    writer.write(
                        (
                            f"POST /mcp HTTP/1.1\r\nHost: {target.netloc}\r\n"
                            "Authorization: Bearer fixture\r\nContent-Type: application/json\r\n"
                            "Accept: application/json, text/event-stream\r\n"
                            "Content-Length: 100\r\n\r\n{"
                        ).encode()
                    )
                    await writer.drain()
                    response = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=1)
                    assert b" 408 " in response.splitlines()[0]
                finally:
                    writer.close()
                    await writer.wait_closed()
                assert (await client.post("/mcp", json=message, headers=headers)).status_code == 200

    asyncio.run(exercise())
