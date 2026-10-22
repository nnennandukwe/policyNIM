"""Opt-in local issuer interoperability; no NVIDIA or production credentials."""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import secrets
import socket
import ssl
import subprocess
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from string import Template
from threading import Thread
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from test_mcp_authorization import METADATA_PATH, RESOURCE, oauth_settings

from policynim.interfaces import mcp as mcp_module
from policynim.services.authorization import (
    OPERATION_SCOPES,
    AuthenticatedPrincipal,
    IntrospectionTokenVerifier,
)
from policynim.types import SearchRequest, SearchResult

KEYCLOAK_IMAGE = (
    "quay.io/keycloak/keycloak:26.7.5@"
    "sha256:37dbaf6f0722c9ec246335f36e1ef8b2e6cb960f7c27e0d8c615121a3d475a85"
)
CLIENT_ID = "policynim-pkce-evaluation"
CALLBACK = "http://127.0.0.1/callback"

pytestmark = [
    pytest.mark.docker_live,
    pytest.mark.skipif(
        os.getenv("POLICYNIM_RUN_KEYCLOAK_TESTS") != "1",
        reason="Set POLICYNIM_RUN_KEYCLOAK_TESTS=1 for disposable local Keycloak checks.",
    ),
]


class LoginForm(HTMLParser):
    """Read only Keycloak's login action, never scripts or page instructions."""

    action: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        """Find the password form using the issuer's stable form identifier."""
        values = dict(attrs)
        if tag == "form" and values.get("id") == "kc-form-login":
            self.action = values.get("action")


def write_tls_fixture(root: Path) -> ssl.SSLContext:
    """Create a short-lived localhost certificate and trust only this fixture CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_path = root / "tls.crt"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (root / "tls.key").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return ssl.create_default_context(cafile=str(cert_path))


def realm_profile(secret: str, password: str) -> dict[str, object]:
    """Configure separate introspection and PKCE clients with resource-bound scopes."""
    template = Path(__file__).resolve().parents[1] / "deploy/keycloak/policynim-realm.json"
    profile = json.loads(
        Template(template.read_text()).substitute(
            POLICYNIM_OAUTH_RESOURCE=RESOURCE,
            POLICYNIM_INTROSPECTION_SECRET=secret,
            POLICYNIM_CHATGPT_CLIENT_ID=CLIENT_ID,
            POLICYNIM_CHATGPT_REDIRECT_URI=CALLBACK,
        )
    )
    profile.update(
        {
            "realm": "policynim-evaluation",
            "accessTokenLifespan": 10,
            "users": [
                {
                    "username": "evaluation-user",
                    "enabled": True,
                    "emailVerified": True,
                    "firstName": "Evaluation",
                    "lastName": "Fixture",
                    "email": "evaluation@example.invalid",
                    "credentials": [{"type": "password", "value": password, "temporary": False}],
                }
            ],
        }
    )
    return profile


@dataclass(repr=False)
class LocalIssuer:
    """Hold disposable fixture credentials outside assertion and diagnostic output."""

    issuer: str
    tls: ssl.SSLContext
    secret: str = field(repr=False)
    password: str = field(repr=False)

    def token_endpoint(self, operation: str = "token") -> str:
        """Resolve fixed endpoints within this test-owned realm."""
        return self.issuer + "/protocol/openid-connect/" + operation


@pytest.fixture
def local_issuer(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[LocalIssuer]:
    """Run one pinned issuer on loopback and remove only its own container on exit."""
    for logger in ("httpx", "httpx2", "httpcore"):
        monkeypatch.setattr(logging.getLogger(logger), "level", logging.WARNING)
    root = tmp_path / "keycloak"
    root.mkdir()
    tls = write_tls_fixture(root)
    secret, password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    (root / "realm.json").write_text(json.dumps(realm_profile(secret, password)))
    # Files contain only this disposable fixture's credentials. Docker's unprivileged
    # user reads them through individual read-only mounts, never a production secret.
    container = "policynim-oauth-" + secrets.token_hex(8)
    command = ["docker", "run", "--detach", "--name", container, "--pull=never"]
    for filename, destination in (
        ("tls.crt", "/tmp/tls.crt"),
        ("tls.key", "/tmp/tls.key"),
        ("realm.json", "/opt/keycloak/data/import/realm.json"),
    ):
        command.extend(["--mount", f"type=bind,src={root / filename},dst={destination},readonly"])
    command.extend(
        [
            "--publish",
            "127.0.0.1::8443",
            KEYCLOAK_IMAGE,
            "start-dev",
            "--import-realm",
            "--http-enabled=false",
            "--https-certificate-file=/tmp/tls.crt",
            "--https-certificate-key-file=/tmp/tls.key",
            "--hostname=localhost",
            "--features=resource-indicators",
        ]
    )
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        if result.returncode:
            pytest.fail("Local issuer couldn't start; check Docker and pull the pinned image.")
        mapping = subprocess.run(
            ["docker", "port", container, "8443/tcp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        address, separator, port = mapping.stdout.strip().rpartition(":")
        assert mapping.returncode == 0 and address == "127.0.0.1" and separator and port.isdigit()
        # Docker owns the port reservation. The localhost-only hostname resolves
        # its dynamic port from the request, and discovery must match it exactly.
        issuer = LocalIssuer(
            f"https://localhost:{port}/realms/policynim-evaluation", tls, secret, password
        )
        with httpx.Client(verify=tls, trust_env=False, timeout=2) as client:
            deadline = time.monotonic() + 90
            while time.monotonic() < deadline:
                try:
                    if (
                        client.get(issuer.issuer + "/.well-known/openid-configuration").status_code
                        == 200
                    ):
                        break
                except httpx.TransportError:
                    pass
                time.sleep(0.25)
            else:
                pytest.fail("Local issuer didn't become ready within 90 seconds.")
        yield issuer
    finally:
        subprocess.run(["docker", "rm", "--force", container], capture_output=True, timeout=30)
        for filename in ("tls.key", "realm.json"):
            (root / filename).unlink(missing_ok=True)


def login_code(
    client: httpx.Client, issuer: LocalIssuer, *, scope: str, resource: str = RESOURCE
) -> tuple[str, str]:
    """Complete real authorization-code login using generated PKCE and state values."""
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=")
    state = secrets.token_urlsafe(24)
    response = client.get(
        issuer.token_endpoint("auth"),
        params={
            "client_id": CLIENT_ID,
            "redirect_uri": CALLBACK,
            "response_type": "code",
            "scope": scope,
            "resource": resource,
            "state": state,
            "code_challenge": challenge.decode(),
            "code_challenge_method": "S256",
        },
    )
    form = LoginForm()
    form.feed(response.text)
    assert form.action is not None, "Issuer didn't offer the expected login form."
    action = urlsplit(form.action)
    assert (action.scheme, action.netloc) == (
        urlsplit(issuer.issuer).scheme,
        urlsplit(issuer.issuer).netloc,
    )
    response = client.post(
        form.action, data={"username": "evaluation-user", "password": issuer.password}
    )
    assert response.status_code == 302, "Issuer login didn't return an authorization response."
    callback = urlsplit(response.headers["location"])
    assert callback.scheme + "://" + callback.netloc + callback.path == CALLBACK
    values = parse_qs(callback.query)
    assert values.get("state") == [state]
    assert values.get("iss") == [issuer.issuer]
    assert "code" in values, "Issuer authorization failed."
    return values["code"][0], verifier


def exchange(client: httpx.Client, issuer: LocalIssuer, code: str, verifier: str) -> httpx.Response:
    """Exchange with the public PKCE client, never the resource client's secret."""
    return client.post(
        issuer.token_endpoint(),
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_ID,
            "code": code,
            "redirect_uri": CALLBACK,
            "code_verifier": verifier,
            "resource": RESOURCE,
        },
    )


def test_keycloak_pkce_resource_refresh_revocation_and_recovery(
    local_issuer: LocalIssuer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cross real issuer TLS and MCP TCP boundaries without any NVIDIA allocation."""
    issuer = local_issuer
    settings = oauth_settings(
        tmp_path,
        mcp_oauth_issuer_url=issuer.issuer,
        mcp_oauth_client_id=RESOURCE,
        mcp_oauth_client_secret=issuer.secret,
    )
    token_verifier = IntrospectionTokenVerifier(
        settings, transport=httpx.AsyncHTTPTransport(verify=issuer.tls, trust_env=False)
    )
    monkeypatch.setattr(
        mcp_module, "IntrospectionTokenVerifier", lambda *args, **kwargs: token_verifier
    )
    principals: list[AuthenticatedPrincipal | None] = []

    class SearchFixture:
        """Return deterministic service output while capturing actual verified identity."""

        def search(
            self, request: SearchRequest, *, principal: AuthenticatedPrincipal | None = None
        ) -> SearchResult:
            """Accept only the real issuer's propagated principal, with no model work."""
            principals.append(principal)
            return SearchResult(query=request.query, top_k=request.top_k, hits=[])

        def close(self) -> None:
            """This deterministic service owns no provider connections."""

    monkeypatch.setattr(mcp_module, "create_search_service", lambda settings: SearchFixture())
    app = mcp_module._build_streamable_http_app(settings)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="on"))
    thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started:
            assert thread.is_alive() and time.monotonic() < deadline, (
                "MCP listener failed to start."
            )
            time.sleep(0.01)
        with (
            httpx.Client(verify=issuer.tls, trust_env=False, timeout=10) as auth,
            httpx.Client(
                base_url=f"http://127.0.0.1:{listener.getsockname()[1]}",
                trust_env=False,
                timeout=10,
            ) as mcp,
        ):
            metadata = mcp.get(METADATA_PATH).json()
            assert metadata["resource"] == RESOURCE
            assert metadata["authorization_servers"] == [issuer.issuer]
            discovery = auth.get(issuer.issuer + "/.well-known/openid-configuration").json()
            assert discovery["issuer"] == issuer.issuer
            assert "S256" in discovery["code_challenge_methods_supported"]
            assert discovery["authorization_response_iss_parameter_supported"] is True
            assert "refresh_token" in discovery["grant_types_supported"]

            def call(token: str | None, name: str = "policy_search") -> httpx.Response:
                """Send each operation through HTTP admission, introspection, and SDK dispatch."""
                headers = {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2026-07-28",
                    "Mcp-Method": "tools/call",
                    "Mcp-Name": name,
                }
                if token is not None:
                    headers["Authorization"] = "Bearer " + token
                return mcp.post(
                    "/mcp",
                    headers=headers,
                    json={
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "tools/call",
                        "params": {
                            "name": name,
                            "arguments": {"query": "fixture"},
                            "_meta": {
                                "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                                "io.modelcontextprotocol/clientCapabilities": {},
                            },
                        },
                    },
                )

            assert call(None).status_code == 401
            assert call("invalid-fixture-token").status_code == 401
            code, verifier = login_code(auth, issuer, scope="openid policies:read")
            tokens_response = exchange(auth, issuer, code, verifier)
            assert tokens_response.status_code == 200
            tokens = tokens_response.json()
            id_token_rejected = call(tokens["id_token"])
            assert id_token_rejected.status_code == 401
            introspected = auth.post(
                issuer.token_endpoint("token/introspect"),
                data={
                    "client_id": RESOURCE,
                    "client_secret": issuer.secret,
                    "token": tokens["access_token"],
                    "token_type_hint": "access_token",
                },
            )
            assert introspected.status_code == 200
            observed = introspected.json()
            expected = {"active", "iss", "sub", "client_id", "aud", "exp", "scope", "token_type"}
            if expected - observed.keys():
                pytest.fail(
                    "Issuer introspection lacks fields: "
                    + ", ".join(sorted(expected - observed.keys()))
                )
            accepted = call(tokens["access_token"])
            assert accepted.status_code == 200
            assert len(principals) == 1 and principals[0] is not None
            assert principals[0].issuer == issuer.issuer and principals[0].client_id == CLIENT_ID
            for operation in OPERATION_SCOPES:
                if operation in {
                    "eval_submit",
                    "eval_inspect",
                    "eval_result",
                    "eval_cancel",
                    "policy_compile",
                    "policy_preflight",
                    "policy_preflight_evidence",
                }:
                    denied = call(tokens["access_token"], operation)
                    assert denied.status_code == 403
                    assert 'error="insufficient_scope"' in denied.headers["WWW-Authenticate"]
            assert len(principals) == 1
            refreshed = auth.post(
                issuer.token_endpoint(),
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": tokens["refresh_token"],
                    "resource": RESOURCE,
                },
            )
            assert refreshed.status_code == 200
            renewed = refreshed.json()
            accepted = call(renewed["access_token"])
            assert accepted.status_code == 200
            assert principals[-1] == principals[0] or (
                principals[-1] is not None and principals[-1].subject == principals[0].subject
            )
            # Revoke the refresh token/session. RFC 7009 access-token revocation alone
            # doesn't promise an immediate session logout in every issuer.
            revoked = auth.post(
                issuer.token_endpoint("revoke"),
                data={
                    "client_id": CLIENT_ID,
                    "token": renewed["refresh_token"],
                    "token_type_hint": "refresh_token",
                },
            )
            assert revoked.status_code == 200
            rejected = call(renewed["access_token"])
            assert rejected.status_code == 401
            revoked_refresh = auth.post(
                issuer.token_endpoint(),
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": renewed["refresh_token"],
                },
            )
            assert revoked_refresh.status_code == 400

            replayed = exchange(auth, issuer, code, verifier)
            assert replayed.status_code == 400  # code replay

            auth.cookies.clear()
            code, verifier = login_code(auth, issuer, scope="policies:read")
            invalid_pkce = exchange(auth, issuer, code, secrets.token_urlsafe(48))
            assert invalid_pkce.status_code == 400
            auth.cookies.clear()
            code, verifier = login_code(auth, issuer, scope="policies:read")
            wrong_resource = auth.post(
                issuer.token_endpoint(),
                data={
                    "grant_type": "authorization_code",
                    "client_id": CLIENT_ID,
                    "code": code,
                    "redirect_uri": CALLBACK,
                    "code_verifier": verifier,
                    "resource": "https://foreign.example/mcp",
                },
            )
            assert wrong_resource.status_code == 400

            auth.cookies.clear()
            code, verifier = login_code(auth, issuer, scope="policies:read policies:evaluate")
            recovered = exchange(auth, issuer, code, verifier)
            assert recovered.status_code == 200
            final_tokens = recovered.json()
            accepted = call(final_tokens["access_token"])
            assert accepted.status_code == 200
            until = time.monotonic() + int(final_tokens["expires_in"]) + 2
            while time.monotonic() < until:
                time.sleep(0.1)
            expired = call(final_tokens["access_token"])
            assert expired.status_code == 401
            after_expiry = auth.post(
                issuer.token_endpoint(),
                data={
                    "grant_type": "refresh_token",
                    "client_id": CLIENT_ID,
                    "refresh_token": final_tokens["refresh_token"],
                    "resource": RESOURCE,
                },
            )
            assert after_expiry.status_code == 200
            logged_out_tokens = after_expiry.json()
            logout = auth.post(
                issuer.token_endpoint("logout"),
                data={
                    "client_id": CLIENT_ID,
                    "refresh_token": logged_out_tokens["refresh_token"],
                },
            )
            assert logout.status_code == 204
            rejected = call(logged_out_tokens["access_token"])
            assert rejected.status_code == 401
            auth.cookies.clear()
            code, verifier = login_code(auth, issuer, scope="policies:read")
            reconnected = exchange(auth, issuer, code, verifier)
            assert reconnected.status_code == 200
            accepted = call(reconnected.json()["access_token"])
            assert accepted.status_code == 200
            assert mcp.get("/healthz").status_code == 503  # sanitized, unindexed test runtime
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()
