"""MCP surface for the public PolicyNIM workflow."""

from __future__ import annotations

import asyncio
import errno
import html
import json
import logging
import secrets
import socket
import sys
import time
from collections.abc import Callable
from contextvars import ContextVar
from pathlib import Path

from mcp.server.fastmcp import Context, FastMCP
from starlette.datastructures import Headers
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.types import ASGIApp, Receive, Scope, Send

from policynim.errors import ConfigurationError, PolicyNIMError, ProviderError
from policynim.services import (
    BetaAuthService,
    create_beta_auth_service,
    create_preflight_service,
    create_runtime_health_service,
    create_search_service,
    ensure_hosted_runtime_ready,
)
from policynim.settings import Settings, get_settings
from policynim.types import (
    MAX_TOP_K,
    MIN_TOP_K,
    BetaAccount,
    BetaUsageSnapshot,
    HealthCheckResult,
    PreflightRequest,
    SearchRequest,
)

SUPPORTED_TRANSPORTS = ("stdio", "streamable-http")
_STREAMABLE_HTTP_PATH = "/mcp"
_HEALTH_PATH = "/healthz"
_BETA_PATH = "/beta"
_FAVICON_PATH = "/favicon.ico"
_AUTH_GITHUB_START_PATH = "/auth/github/start"
_AUTH_GITHUB_CALLBACK_PATH = "/auth/github/callback"
_BETA_API_KEY_REGENERATE_PATH = "/beta/api-key/regenerate"
_BETA_LOGOUT_PATH = "/beta/logout"
_BETA_ASSET_PATH = "/beta/assets"
_BETA_LIGHT_LOGO_FILENAME = "policynim_lightmode.png"
_BETA_DARK_LOGO_FILENAME = "policynim_darkmode.jpg"
_BETA_LIGHT_LOGO_ROUTE = f"{_BETA_ASSET_PATH}/{_BETA_LIGHT_LOGO_FILENAME}"
_BETA_DARK_LOGO_ROUTE = f"{_BETA_ASSET_PATH}/{_BETA_DARK_LOGO_FILENAME}"
_BETA_ACCOUNT_SESSION_KEY = "beta_account_id"
_BETA_GITHUB_STATE_SESSION_KEY = "beta_github_oauth_state"
_BETA_PAGE_STYLES = """
:root,
html[data-theme="light"] {
  --beta-radius-xl: 30px;
  --beta-radius-lg: 22px;
  --beta-radius-md: 16px;
  color-scheme: light;
  --beta-bg: #edf2eb;
  --beta-bg-strong: #dbe6d7;
  --beta-surface: rgba(255, 255, 255, 0.78);
  --beta-surface-strong: rgba(255, 255, 255, 0.94);
  --beta-surface-muted: rgba(243, 248, 241, 0.86);
  --beta-border: rgba(28, 44, 29, 0.12);
  --beta-text: #17211a;
  --beta-text-muted: #516252;
  --beta-heading: #102d17;
  --beta-primary: #58b947;
  --beta-primary-strong: #2f8f34;
  --beta-primary-soft: rgba(88, 185, 71, 0.14);
  --beta-warning: #8a5a00;
  --beta-warning-soft: rgba(255, 207, 102, 0.24);
  --beta-danger: #a7372f;
  --beta-danger-soft: rgba(236, 95, 85, 0.14);
  --beta-success: #0d7a50;
  --beta-success-soft: rgba(20, 184, 112, 0.14);
  --beta-shadow: 0 24px 60px rgba(16, 45, 23, 0.12);
  --beta-shadow-soft: 0 12px 30px rgba(16, 45, 23, 0.08);
  --beta-code-bg: #f5f8f4;
  --beta-code-border: rgba(47, 143, 52, 0.18);
}

html[data-theme="dark"] {
  color-scheme: dark;
  --beta-bg: #111715;
  --beta-bg-strong: #18201d;
  --beta-surface: rgba(18, 26, 22, 0.84);
  --beta-surface-strong: rgba(24, 33, 29, 0.94);
  --beta-surface-muted: rgba(19, 28, 24, 0.92);
  --beta-border: rgba(129, 203, 116, 0.18);
  --beta-text: #e6f0e6;
  --beta-text-muted: #a3b5a4;
  --beta-heading: #f4faf4;
  --beta-primary: #74d260;
  --beta-primary-strong: #98e67e;
  --beta-primary-soft: rgba(116, 210, 96, 0.16);
  --beta-warning: #ffd276;
  --beta-warning-soft: rgba(255, 210, 118, 0.14);
  --beta-danger: #ff8e82;
  --beta-danger-soft: rgba(255, 142, 130, 0.14);
  --beta-success: #6de0ae;
  --beta-success-soft: rgba(109, 224, 174, 0.14);
  --beta-shadow: 0 30px 70px rgba(0, 0, 0, 0.45);
  --beta-shadow-soft: 0 16px 36px rgba(0, 0, 0, 0.3);
  --beta-code-bg: rgba(10, 15, 12, 0.92);
  --beta-code-border: rgba(116, 210, 96, 0.2);
}

* {
  box-sizing: border-box;
}

html,
body {
  margin: 0;
  min-height: 100%;
}

body.beta-page {
  font-family: "Avenir Next", "Segoe UI", "Helvetica Neue", sans-serif;
  background:
    radial-gradient(circle at top left, rgba(116, 210, 96, 0.18), transparent 32%),
    radial-gradient(circle at top right, rgba(16, 45, 23, 0.18), transparent 38%),
    linear-gradient(180deg, var(--beta-bg) 0%, var(--beta-bg-strong) 100%);
  color: var(--beta-text);
  line-height: 1.6;
}

a {
  color: inherit;
}

.beta-page__backdrop {
  position: fixed;
  inset: 0;
  pointer-events: none;
  background:
    radial-gradient(circle at 15% 20%, rgba(88, 185, 71, 0.1), transparent 22%),
    radial-gradient(circle at 85% 0%, rgba(88, 185, 71, 0.12), transparent 28%);
}

.beta-shell {
  position: relative;
  z-index: 1;
  width: min(1160px, calc(100% - 32px));
  margin: 0 auto;
  padding: 48px 0 72px;
}

.beta-shell__utility {
  display: flex;
  justify-content: flex-end;
  margin-bottom: 14px;
}

.beta-theme-toggle {
  appearance: none;
  display: inline-flex;
  align-items: center;
  gap: 10px;
  min-height: 44px;
  padding: 0 16px;
  border-radius: 999px;
  border: 1px solid var(--beta-border);
  background: var(--beta-surface-strong);
  color: var(--beta-text);
  font: inherit;
  font-weight: 700;
  cursor: pointer;
  box-shadow: var(--beta-shadow-soft);
}

.beta-theme-toggle:hover {
  transform: translateY(-1px);
}

.beta-theme-toggle:focus-visible {
  outline: 3px solid var(--beta-primary-soft);
  outline-offset: 2px;
}

.beta-theme-toggle__icon {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 1.7rem;
  height: 1.7rem;
  border-radius: 999px;
  background: var(--beta-primary-soft);
  color: var(--beta-primary-strong);
  font-size: 0.92rem;
}

.beta-topbar,
.beta-hero,
.beta-card,
.beta-panel,
.beta-callout {
  border: 1px solid var(--beta-border);
  background: var(--beta-surface);
  backdrop-filter: blur(16px);
  box-shadow: var(--beta-shadow-soft);
}

.beta-topbar,
.beta-hero {
  border-radius: var(--beta-radius-xl);
}

.beta-card,
.beta-panel,
.beta-callout {
  border-radius: var(--beta-radius-lg);
}

.beta-topbar,
.beta-hero,
.beta-card,
.beta-panel,
.beta-callout,
.beta-notice {
  overflow: hidden;
}

.beta-topbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 24px;
  padding: 22px 28px;
  margin-bottom: 24px;
}

.beta-brand {
  display: flex;
  align-items: center;
  gap: 18px;
  min-width: 0;
}

.beta-brand__copy {
  min-width: 0;
}

.beta-kicker,
.beta-eyebrow {
  margin: 0 0 8px;
  font-size: 0.78rem;
  font-weight: 700;
  letter-spacing: 0.16em;
  text-transform: uppercase;
  color: var(--beta-primary-strong);
}

.beta-eyebrow {
  font-size: 0.74rem;
}

.beta-title,
.beta-section-title,
.beta-card-title {
  margin: 0;
  font-family: "Iowan Old Style", "Palatino Linotype", "Book Antiqua", serif;
  color: var(--beta-heading);
  line-height: 1.05;
  text-wrap: balance;
}

.beta-title {
  max-width: 10ch;
  font-size: clamp(2.3rem, 5vw, 3.7rem);
}

.beta-topbar .beta-title {
  max-width: 12ch;
  font-size: clamp(1.8rem, 3.2vw, 2.45rem);
}

.beta-section-title {
  font-size: clamp(1.7rem, 3vw, 2.5rem);
}

.beta-card-title {
  font-size: 1.45rem;
}

.beta-subtitle,
.beta-body-copy,
.beta-fineprint,
.beta-inline-hint,
.beta-meta-label,
.beta-command-description,
.beta-notice__body,
.beta-status-pill,
.beta-facts dt {
  color: var(--beta-text-muted);
}

.beta-subtitle,
.beta-body-copy {
  margin: 0;
  font-size: 1.04rem;
}

.beta-hero {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(300px, 0.9fr);
  gap: 28px;
  padding: 30px;
  margin-bottom: 24px;
}

.beta-hero__content {
  display: flex;
  flex-direction: column;
  gap: 18px;
}

.beta-hero__content .beta-lockup {
  width: min(420px, 100%);
}

.beta-lockup,
.beta-lockup__image {
  display: block;
  max-width: 100%;
}

.beta-lockup {
  position: relative;
}

.beta-lockup__image {
  width: 100%;
  height: auto;
}

.beta-lockup__image--dark {
  display: none;
}

html[data-theme="dark"] .beta-lockup__image--light {
  display: none;
}

html[data-theme="dark"] .beta-lockup__image--dark {
  display: block;
}

.beta-lockup--compact {
  width: 220px;
  flex: 0 0 auto;
}

.beta-callout {
  position: relative;
  padding: 24px;
  background:
    linear-gradient(180deg, rgba(88, 185, 71, 0.11), transparent 52%),
    var(--beta-surface-strong);
}

.beta-callout::after {
  content: "";
  position: absolute;
  inset: auto -18% -42% auto;
  width: 220px;
  height: 220px;
  border-radius: 50%;
  background: radial-gradient(circle, rgba(88, 185, 71, 0.16), transparent 70%);
  pointer-events: none;
}

.beta-action-row,
.beta-button-row,
.beta-command-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 12px;
}

.beta-button-row form {
  margin: 0;
}

.beta-button,
.beta-copy-button {
  appearance: none;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 48px;
  padding: 0 18px;
  border-radius: 999px;
  border: 1px solid transparent;
  font: inherit;
  font-weight: 700;
  text-decoration: none;
  cursor: pointer;
  transition:
    transform 120ms ease,
    background-color 120ms ease,
    border-color 120ms ease,
    color 120ms ease,
    box-shadow 120ms ease;
}

.beta-button:hover,
.beta-copy-button:hover {
  transform: translateY(-1px);
}

.beta-button:focus-visible,
.beta-copy-button:focus-visible {
  outline: 3px solid var(--beta-primary-soft);
  outline-offset: 2px;
}

.beta-button--primary {
  background: linear-gradient(135deg, var(--beta-primary), var(--beta-primary-strong));
  color: #061009;
  box-shadow: 0 14px 32px rgba(88, 185, 71, 0.26);
}

.beta-button--secondary {
  border-color: var(--beta-border);
  background: var(--beta-surface-strong);
  color: var(--beta-text);
}

.beta-copy-button {
  display: none;
  min-height: 40px;
  padding: 0 14px;
  border-color: var(--beta-border);
  background: transparent;
  color: var(--beta-text);
}

body[data-js="ready"] .beta-copy-button {
  display: inline-flex;
}

.beta-section {
  margin-top: 28px;
}

.beta-section__header {
  margin-bottom: 16px;
}

.beta-card-grid {
  display: grid;
  gap: 18px;
}

.beta-card-grid--steps {
  grid-template-columns: repeat(3, minmax(0, 1fr));
}

.beta-card-grid--dashboard,
.beta-card-grid--commands {
  grid-template-columns: repeat(2, minmax(0, 1fr));
}

.beta-card,
.beta-panel {
  padding: 24px;
}

.beta-card__header,
.beta-command-header {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: flex-start;
}

.beta-notice-stack {
  display: grid;
  gap: 12px;
}

.beta-notice {
  border-radius: var(--beta-radius-md);
  border: 1px solid transparent;
  padding: 16px 18px;
}

.beta-notice--error {
  border-color: rgba(167, 55, 47, 0.24);
  background: var(--beta-danger-soft);
}

.beta-notice--success {
  border-color: rgba(13, 122, 80, 0.24);
  background: var(--beta-success-soft);
}

.beta-notice--warning {
  border-color: rgba(138, 90, 0, 0.24);
  background: var(--beta-warning-soft);
}

.beta-notice__title {
  margin: 0 0 4px;
  color: var(--beta-heading);
  font-weight: 700;
}

.beta-notice__body {
  margin: 0;
}

.beta-inline-code,
.beta-command-block {
  border-radius: var(--beta-radius-md);
  border: 1px solid var(--beta-code-border);
  background: var(--beta-code-bg);
}

.beta-inline-code {
  margin: 16px 0;
  padding: 12px 14px;
  font-family: "SFMono-Regular", "SF Mono", "Consolas", monospace;
  overflow-wrap: anywhere;
}

.beta-meta-list,
.beta-facts {
  display: grid;
  gap: 14px;
}

.beta-meta-row,
.beta-facts__row {
  display: grid;
  gap: 4px;
}

.beta-meta-label,
.beta-facts dt {
  font-size: 0.86rem;
  letter-spacing: 0.04em;
  text-transform: uppercase;
}

.beta-meta-row a {
  text-decoration: underline;
  text-underline-offset: 0.2em;
}

.beta-facts {
  margin: 0;
}

.beta-facts__row {
  padding-bottom: 14px;
  border-bottom: 1px solid rgba(28, 44, 29, 0.08);
}

.beta-facts__row:last-child {
  padding-bottom: 0;
  border-bottom: 0;
}

.beta-facts dt,
.beta-facts dd {
  margin: 0;
}

.beta-facts dd {
  color: var(--beta-text);
  font-size: 1rem;
  overflow-wrap: anywhere;
}

.beta-status-pill {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 38px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1px solid var(--beta-border);
  background: var(--beta-surface-muted);
  font-weight: 700;
}

.beta-status-pill::before {
  content: "";
  width: 9px;
  height: 9px;
  border-radius: 50%;
  background: var(--beta-text-muted);
}

.beta-status-pill--active::before {
  background: var(--beta-success);
}

.beta-status-pill--suspended::before {
  background: var(--beta-warning);
}

.beta-usage {
  margin-top: 20px;
}

.beta-usage__row {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  align-items: baseline;
  margin-bottom: 10px;
}

.beta-usage__bar {
  position: relative;
  height: 12px;
  border-radius: 999px;
  background: rgba(28, 44, 29, 0.08);
  overflow: hidden;
}

.beta-usage__bar span {
  position: absolute;
  inset: 0 auto 0 0;
  border-radius: inherit;
  background: linear-gradient(135deg, var(--beta-primary), var(--beta-primary-strong));
}

.beta-command-block {
  margin: 16px 0 0;
  padding: 16px;
  white-space: pre-wrap;
  word-break: break-word;
  font-family: "SFMono-Regular", "SF Mono", "Consolas", monospace;
  color: var(--beta-text);
}

.beta-command-description,
.beta-fineprint,
.beta-inline-hint {
  margin: 0;
  font-size: 0.94rem;
}

.beta-grid-note {
  margin-top: 16px;
}

.beta-step-index {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  margin-bottom: 16px;
  background: var(--beta-primary-soft);
  color: var(--beta-primary-strong);
  font-weight: 800;
}

.beta-card--emphasis {
  background:
    linear-gradient(180deg, rgba(88, 185, 71, 0.12), transparent 55%),
    var(--beta-surface-strong);
}

.beta-card--secret {
  background:
    linear-gradient(180deg, rgba(13, 122, 80, 0.11), transparent 55%),
    var(--beta-surface-strong);
}

@media (max-width: 900px) {
  .beta-shell {
    width: min(100% - 24px, 1160px);
    padding: 24px 0 48px;
  }

  .beta-topbar,
  .beta-hero,
  .beta-card-grid--dashboard,
  .beta-card-grid--commands,
  .beta-card-grid--steps {
    grid-template-columns: 1fr;
  }

  .beta-topbar {
    padding: 20px;
  }

  .beta-hero,
  .beta-card,
  .beta-panel {
    padding: 20px;
  }

  .beta-brand {
    flex-direction: column;
    align-items: flex-start;
  }
}

@media (max-width: 640px) {
  .beta-shell {
    width: min(100% - 18px, 1160px);
  }

  .beta-topbar,
  .beta-hero,
  .beta-card,
  .beta-panel {
    border-radius: 22px;
  }

  .beta-topbar,
  .beta-card__header,
  .beta-command-header,
  .beta-usage__row {
    flex-direction: column;
    align-items: flex-start;
  }
}
"""
_BETA_THEME_INIT_SCRIPT = """
(() => {
  const root = document.documentElement;
  if (!root) {
    return;
  }
  let theme = "";
  try {
    theme = window.localStorage.getItem("policynim-beta-theme") || "";
  } catch (error) {
    theme = "";
  }
  if (theme !== "light" && theme !== "dark") {
    theme = window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  root.dataset.theme = theme;
})();
"""
_BETA_PAGE_SCRIPT = """
(() => {
  const root = document.documentElement;
  const body = document.body;
  if (!root || !body) {
    return;
  }
  const toggleButton = document.querySelector("[data-theme-toggle]");
  const toggleLabel = toggleButton?.querySelector("[data-theme-label]");
  const applyTheme = (theme) => {
    root.dataset.theme = theme;
    if (toggleButton) {
      toggleButton.setAttribute("aria-pressed", String(theme === "dark"));
      toggleButton.setAttribute(
        "title",
        theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
      );
    }
    if (toggleLabel) {
      toggleLabel.textContent = theme === "dark" ? "Theme: Dark" : "Theme: Light";
    }
  };
  applyTheme(root.dataset.theme === "dark" ? "dark" : "light");
  toggleButton?.addEventListener("click", () => {
    const nextTheme = root.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(nextTheme);
    try {
      window.localStorage.setItem("policynim-beta-theme", nextTheme);
    } catch (error) {
      // Ignore storage failures and keep the in-memory theme state.
    }
  });
  body.dataset.js = "ready";
  for (const button of document.querySelectorAll("[data-copy]")) {
    button.addEventListener("click", async () => {
      const originalLabel = button.textContent || "Copy command";
      const text = button.getAttribute("data-copy") || "";
      try {
        if (!navigator.clipboard || !window.isSecureContext) {
          throw new Error("Clipboard access unavailable.");
        }
        await navigator.clipboard.writeText(text);
        button.textContent = "Copied";
      } catch (error) {
        window.prompt("Copy this command:", text);
        button.textContent = "Copy again";
      } finally {
        window.setTimeout(() => {
          button.textContent = originalLabel;
        }, 1400);
      }
    });
  }
})();
"""
LOGGER = logging.getLogger(__name__)
_HOSTED_LOGGER_NAME = "policynim.hosted"
_HOSTED_AUTH_RESULT: ContextVar[str] = ContextVar(
    "policynim_hosted_auth_result",
    default="not_required",
)


class _InMemoryRateLimiter:
    """Process-local sliding-window throttling for the GitHub auth routes."""

    def __init__(self, *, max_attempts: int, window_seconds: int) -> None:
        self._max_attempts = max_attempts
        self._window_seconds = window_seconds
        self._attempts: dict[str, list[float]] = {}

    def allow(self, key: str, *, now: float | None = None) -> bool:
        timestamp = time.monotonic() if now is None else now
        attempts = [
            attempt
            for attempt in self._attempts.get(key, [])
            if timestamp - attempt < self._window_seconds
        ]
        if len(attempts) >= self._max_attempts:
            self._attempts[key] = attempts
            return False
        attempts.append(timestamp)
        self._attempts[key] = attempts
        return True

    def reset(self) -> None:
        """Clear all in-memory rate-limit state."""
        self._attempts.clear()


def _resolve_top_k(top_k: int | None) -> int:
    """Resolve and validate top_k across MCP tools."""
    resolved = top_k if top_k is not None else get_settings().default_top_k
    _validate_top_k(resolved)
    return resolved


def _validate_top_k(top_k: int) -> None:
    """Validate top_k across MCP tools."""
    if not MIN_TOP_K <= top_k <= MAX_TOP_K:
        raise ValueError(f"top_k must be between {MIN_TOP_K} and {MAX_TOP_K}.")


def _close_service(service: object | None) -> None:
    close = getattr(service, "close", None)
    if callable(close):
        close()


def _configure_hosted_logger() -> None:
    """Emit hosted MCP telemetry as one JSON object per line."""
    logger = logging.getLogger(_HOSTED_LOGGER_NAME)
    if getattr(logger, "_policynim_configured", False):
        return

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    setattr(logger, "_policynim_configured", True)


def _emit_hosted_event(
    event: str,
    *,
    auth_result: str,
    tool_name: str | None,
    latency_ms: float | None,
    upstream_failure_class: str | None,
    request_id: str | None,
) -> None:
    payload = {
        "event": event,
        "auth_result": auth_result,
        "tool_name": tool_name,
        "latency_ms": latency_ms,
        "upstream_failure_class": upstream_failure_class,
        "request_id": request_id,
    }
    logging.getLogger(_HOSTED_LOGGER_NAME).info(json.dumps(payload, sort_keys=True))


def _elapsed_ms(start_time: float) -> float:
    return round((time.perf_counter() - start_time) * 1000, 2)


def _failure_class_from_error(exc: BaseException) -> str | None:
    current: BaseException | None = exc
    while current is not None:
        failure_class = getattr(current, "failure_class", None)
        if isinstance(failure_class, str) and failure_class:
            return failure_class
        current = current.__cause__ or current.__context__
    return None


def _request_id_from_context(ctx: Context) -> str | None:
    try:
        return ctx.request_id
    except ValueError:
        return None


def _streamable_http_port_in_use_message(host: str, port: int) -> str:
    """Return a concrete recovery message for MCP port collisions."""
    return (
        f"Could not start streamable-http MCP server on {host}:{port} "
        "because the port is already in use. "
        "Stop the conflicting process or set `POLICYNIM_MCP_PORT` to another open port. "
        "If the eval UI is also running, check `POLICYNIM_EVAL_UI_PORT` as well."
    )


def _ensure_streamable_http_port_available(host: str, port: int) -> None:
    """Fail early with a clear error when the HTTP MCP port is already occupied."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise ConfigurationError(_streamable_http_port_in_use_message(host, port)) from exc
        raise ConfigurationError(
            f"Could not reserve streamable-http MCP server port {host}:{port}: {exc}."
        ) from exc


def _run_policy_preflight(
    task: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Return policy guidance for a coding task."""
    resolved_top_k = _resolve_top_k(top_k)
    service = create_preflight_service(get_settings())
    try:
        result = service.preflight(PreflightRequest(task=task, domain=domain, top_k=resolved_top_k))
        return result.model_dump(mode="json")
    finally:
        _close_service(service)


def policy_preflight(
    task: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Return policy guidance for a coding task."""
    return _run_policy_preflight(task=task, domain=domain, top_k=top_k)


def _run_policy_search(
    query: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Search the policy corpus."""
    resolved_top_k = _resolve_top_k(top_k)
    service = create_search_service(get_settings())
    try:
        result = service.search(SearchRequest(query=query, domain=domain, top_k=resolved_top_k))
        return result.model_dump(mode="json")
    finally:
        _close_service(service)


def policy_search(
    query: str,
    domain: str | None = None,
    top_k: int | None = None,
) -> dict[str, object]:
    """Search the policy corpus."""
    return _run_policy_search(query=query, domain=domain, top_k=top_k)


def _run_logged_tool(
    tool_name: str,
    operation: Callable[[], dict[str, object]],
    *,
    ctx: Context,
) -> dict[str, object]:
    start_time = time.perf_counter()
    auth_result = _HOSTED_AUTH_RESULT.get()
    request_id = _request_id_from_context(ctx)
    try:
        result = operation()
    except Exception as exc:
        _emit_hosted_event(
            "mcp.tool",
            auth_result=auth_result,
            tool_name=tool_name,
            latency_ms=_elapsed_ms(start_time),
            upstream_failure_class=_failure_class_from_error(exc),
            request_id=request_id,
        )
        raise

    _emit_hosted_event(
        "mcp.tool",
        auth_result=auth_result,
        tool_name=tool_name,
        latency_ms=_elapsed_ms(start_time),
        upstream_failure_class=None,
        request_id=request_id,
    )
    return result


def _policy_preflight_tool(
    task: str,
    domain: str | None = None,
    top_k: int | None = None,
    *,
    ctx: Context,
) -> dict[str, object]:
    return _run_logged_tool(
        "policy_preflight",
        lambda: _run_policy_preflight(task=task, domain=domain, top_k=top_k),
        ctx=ctx,
    )


def _policy_search_tool(
    query: str,
    domain: str | None = None,
    top_k: int | None = None,
    *,
    ctx: Context,
) -> dict[str, object]:
    return _run_logged_tool(
        "policy_search",
        lambda: _run_policy_search(query=query, domain=domain, top_k=top_k),
        ctx=ctx,
    )


def _register_tools(server: FastMCP) -> FastMCP:
    """Register the public MCP tools on the supplied server instance."""
    server.tool(name="policy_preflight")(_policy_preflight_tool)
    server.tool(name="policy_search")(_policy_search_tool)
    return server


def _create_mcp_server(
    settings: Settings,
    *,
    beta_auth_service: BetaAuthService | None = None,
) -> FastMCP:
    """Create a fresh MCP server configured from runtime settings."""
    server = FastMCP(
        "PolicyNIM",
        json_response=True,
        host=settings.mcp_host,
        port=settings.mcp_port,
        streamable_http_path=_STREAMABLE_HTTP_PATH,
    )
    _register_tools(server)
    _register_health_route(server, settings)
    if settings.beta_signup_enabled and beta_auth_service is not None:
        _register_beta_routes(server, settings, beta_auth_service)
    return server


def _register_beta_routes(
    server: FastMCP,
    settings: Settings,
    beta_auth_service: BetaAuthService,
) -> None:
    """Register the hosted beta portal routes."""
    limiter = _InMemoryRateLimiter(
        max_attempts=settings.beta_auth_rate_limit_max_attempts,
        window_seconds=settings.beta_auth_rate_limit_window_seconds,
    )
    trust_forwarded_headers = _beta_session_https_only(settings)

    def _rate_limited(request: Request) -> HTMLResponse | None:
        client_ip = _client_address(
            request,
            trust_forwarded_headers=trust_forwarded_headers,
        )
        if limiter.allow(f"{request.url.path}:{client_ip}"):
            return None
        return _render_beta_landing(
            settings,
            message=(
                "Too many beta authentication attempts from this IP. "
                "Retry after the rate-limit window."
            ),
            status_code=429,
        )

    @server.custom_route(_BETA_LIGHT_LOGO_ROUTE, methods=["GET"], include_in_schema=False)
    async def beta_light_logo(_: Request) -> Response:
        return _render_beta_asset(_BETA_LIGHT_LOGO_FILENAME, media_type="image/png")

    @server.custom_route(_BETA_DARK_LOGO_ROUTE, methods=["GET"], include_in_schema=False)
    async def beta_dark_logo(_: Request) -> Response:
        return _render_beta_asset(_BETA_DARK_LOGO_FILENAME, media_type="image/jpeg")

    @server.custom_route(_FAVICON_PATH, methods=["GET"], include_in_schema=False)
    async def favicon(_: Request) -> Response:
        return _render_beta_asset(_BETA_LIGHT_LOGO_FILENAME, media_type="image/png")

    @server.custom_route(_BETA_PATH, methods=["GET"], include_in_schema=False)
    async def beta_dashboard(request: Request) -> Response:
        account_id = _require_beta_session_account_id(request)
        if account_id is None:
            return _render_beta_landing(settings)

        account = beta_auth_service.get_account(account_id)
        if account is None:
            request.session.clear()
            return _render_beta_landing(
                settings,
                message="Your hosted beta session expired. Sign in again to continue.",
            )
        usage = beta_auth_service.get_portal_usage(account_id)
        return _render_beta_dashboard(settings, account=account, usage=usage)

    @server.custom_route(_AUTH_GITHUB_START_PATH, methods=["GET"], include_in_schema=False)
    async def github_start(request: Request) -> Response:
        blocked = _rate_limited(request)
        if blocked is not None:
            return blocked
        state = secrets.token_urlsafe(24)
        request.session[_BETA_GITHUB_STATE_SESSION_KEY] = state
        return RedirectResponse(
            beta_auth_service.build_github_authorize_url(state=state),
            status_code=302,
        )

    @server.custom_route(_AUTH_GITHUB_CALLBACK_PATH, methods=["GET"], include_in_schema=False)
    async def github_callback(request: Request) -> Response:
        blocked = _rate_limited(request)
        if blocked is not None:
            return blocked
        error = str(request.query_params.get("error") or "").strip()
        if error:
            return _render_beta_landing(
                settings,
                message=f"GitHub sign-in failed: {error}. Retry the sign-in flow.",
            )

        expected_state = request.session.pop(_BETA_GITHUB_STATE_SESSION_KEY, None)
        returned_state = str(request.query_params.get("state") or "").strip()
        if not expected_state or not returned_state or returned_state != expected_state:
            return _render_beta_landing(
                settings,
                message=(
                    "GitHub sign-in failed because the OAuth state was missing or invalid. "
                    "Start the sign-in flow again from /beta."
                ),
                status_code=400,
            )

        code = str(request.query_params.get("code") or "").strip()
        try:
            account = beta_auth_service.complete_github_oauth(code=code)
        except (PolicyNIMError, ProviderError) as exc:
            return _render_beta_landing(settings, message=str(exc), status_code=502)
        except Exception:
            LOGGER.exception("Unexpected hosted beta OAuth callback failure.")
            return _render_beta_landing(
                settings,
                message=(
                    "GitHub sign-in failed due to an unexpected upstream error. "
                    "Retry the sign-in flow."
                ),
                status_code=502,
            )

        request.session[_BETA_ACCOUNT_SESSION_KEY] = account.account_id
        return RedirectResponse(_BETA_PATH, status_code=302)

    @server.custom_route(_BETA_API_KEY_REGENERATE_PATH, methods=["POST"], include_in_schema=False)
    async def beta_regenerate_api_key(request: Request) -> Response:
        account_id = _require_beta_session_account_id(request)
        if account_id is None:
            return RedirectResponse(_BETA_PATH, status_code=302)
        account = beta_auth_service.get_account(account_id)
        if account is None:
            request.session.clear()
            return RedirectResponse(_BETA_PATH, status_code=302)
        try:
            issued_key = beta_auth_service.issue_api_key(account_id=account_id)
        except PolicyNIMError as exc:
            usage = beta_auth_service.get_portal_usage(account_id)
            return _render_beta_dashboard(
                settings,
                account=account,
                usage=usage,
                message=str(exc),
                message_tone="error",
            )
        return _render_beta_dashboard(
            settings,
            account=issued_key.account,
            usage=issued_key.usage,
            new_api_key=issued_key.api_key,
            message="API key generated. Export `POLICYNIM_TOKEN` before connecting your client.",
            message_tone="success",
        )

    @server.custom_route(_BETA_LOGOUT_PATH, methods=["POST"], include_in_schema=False)
    async def beta_logout(request: Request) -> Response:
        request.session.clear()
        return RedirectResponse(_BETA_PATH, status_code=302)


def _register_health_route(server: FastMCP, settings: Settings) -> None:
    """Register a public readiness endpoint for hosted HTTP runtimes."""
    try:
        health_service = create_runtime_health_service(settings)
    except Exception:
        LOGGER.exception("Could not construct runtime health service.")
        health_service = None
    fallback_reason = "Local index readiness could not be inspected."

    def _fallback_result() -> JSONResponse:
        result = HealthCheckResult(
            status="error",
            ready=False,
            table_name=settings.lancedb_table,
            row_count=0,
            mcp_url=_derive_mcp_url(settings),
            reason=fallback_reason,
        )
        return JSONResponse(result.model_dump(mode="json"), status_code=503)

    @server.custom_route(_HEALTH_PATH, methods=["GET"], include_in_schema=False)
    async def healthz(_: Request) -> Response:
        if health_service is None:
            return _fallback_result()

        try:
            result = await asyncio.to_thread(health_service.check)
        except Exception:
            LOGGER.exception("Runtime health probe failed.")
            return _fallback_result()

        status_code = 200 if result.ready else 503
        return JSONResponse(result.model_dump(mode="json"), status_code=status_code)


def _derive_mcp_url(settings: Settings) -> str | None:
    if settings.mcp_public_base_url is None:
        return None
    return str(settings.mcp_public_base_url).rstrip("/") + "/mcp"


def _derive_beta_url(settings: Settings) -> str | None:
    if settings.mcp_public_base_url is None:
        return None
    return str(settings.mcp_public_base_url).rstrip("/") + _BETA_PATH


def _beta_asset_path(filename: str) -> Path:
    return Path(__file__).resolve().parent.parent / "assets" / "beta" / filename


def _render_beta_asset(filename: str, *, media_type: str) -> Response:
    asset_path = _beta_asset_path(filename)
    if not asset_path.is_file():
        return Response("Missing beta asset.", status_code=404)
    return Response(
        asset_path.read_bytes(),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _render_beta_logo(*, alt: str, compact: bool = False) -> str:
    classes = "beta-lockup"
    if compact:
        classes += " beta-lockup--compact"
    return f"""
<div class="{classes}" role="img" aria-label="{html.escape(alt, quote=True)}">
  <img
    class="beta-lockup__image beta-lockup__image--light"
    src="{_BETA_LIGHT_LOGO_ROUTE}"
    alt=""
    aria-hidden="true"
  >
  <img
    class="beta-lockup__image beta-lockup__image--dark"
    src="{_BETA_DARK_LOGO_ROUTE}"
    alt=""
    aria-hidden="true"
  >
</div>
"""


def _render_beta_theme_toggle() -> str:
    return """
<button
  type="button"
  class="beta-theme-toggle"
  data-theme-toggle
  aria-pressed="false"
  title="Switch to dark mode"
>
  <span class="beta-theme-toggle__icon" aria-hidden="true">◐</span>
  <span data-theme-label>Theme</span>
</button>
"""


def _render_beta_notice(*, title: str, message: str, tone: str) -> str:
    return f"""
<section class="beta-notice beta-notice--{tone}" role="status">
  <p class="beta-notice__title">{html.escape(title)}</p>
  <p class="beta-notice__body">{html.escape(message)}</p>
</section>
"""


def _render_copyable_command(
    *,
    title: str,
    description: str,
    command: str,
    button_label: str = "Copy command",
) -> str:
    return f"""
<section class="beta-card">
  <div class="beta-command-header">
    <div>
      <p class="beta-eyebrow">Client setup</p>
      <h2 class="beta-card-title">{html.escape(title)}</h2>
    </div>
    <div class="beta-command-actions">
      <button
        type="button"
        class="beta-copy-button"
        data-copy="{html.escape(command, quote=True)}"
      >
        {html.escape(button_label)}
      </button>
    </div>
  </div>
  <p class="beta-command-description">{html.escape(description)}</p>
  <pre class="beta-command-block">{html.escape(command)}</pre>
</section>
"""


def _render_beta_page(*, page_class: str, content: str) -> HTMLResponse:
    body = f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <meta name="color-scheme" content="light dark">
    <link rel="icon" type="image/png" href="{_BETA_LIGHT_LOGO_ROUTE}">
    <title>PolicyNIM Hosted Beta</title>
    <script>{_BETA_THEME_INIT_SCRIPT}</script>
    <style>{_BETA_PAGE_STYLES}</style>
  </head>
  <body class="beta-page {page_class}">
    <div class="beta-page__backdrop" aria-hidden="true"></div>
    {content}
    <script>{_BETA_PAGE_SCRIPT}</script>
  </body>
</html>
"""
    return HTMLResponse(body)


def _beta_usage_percent(usage: BetaUsageSnapshot) -> int:
    return max(0, min(100, round((usage.request_count / usage.quota) * 100)))


def _render_beta_landing(
    settings: Settings,
    *,
    message: str | None = None,
    status_code: int = 200,
) -> HTMLResponse:
    portal_url = _derive_beta_url(settings) or _BETA_PATH
    mcp_url = _derive_mcp_url(settings) or _STREAMABLE_HTTP_PATH
    portal_url_html = html.escape(portal_url)
    mcp_url_html = html.escape(mcp_url)
    notice_html = ""
    if message:
        notice_html = (
            '<div class="beta-notice-stack">'
            + _render_beta_notice(
                title="Attention required",
                message=message,
                tone="error",
            )
            + "</div>"
        )
    content = f"""
<main class="beta-shell beta-shell--landing">
  <div class="beta-shell__utility">
    {_render_beta_theme_toggle()}
  </div>
  <section class="beta-hero">
    <div class="beta-hero__content">
      <p class="beta-kicker">Hosted MCP beta</p>
      {_render_beta_logo(alt="PolicyNIM")}
      <h1 class="beta-title">PolicyNIM Hosted Beta</h1>
      <p class="beta-subtitle">
        PolicyNIM is a policy-aware engineering preflight layer for AI coding agents, exposed
        here as a hosted MCP endpoint.
      </p>
      <p class="beta-body-copy">
        Sign in with GitHub, issue a hosted MCP API key, and connect your coding client without
        waiting on an operator handoff.
      </p>
      <p class="beta-body-copy">
        The portal keeps the hosted workflow self-serve: authenticate once, generate a key, and
        paste the ready-to-run setup commands into Codex or Claude Code.
      </p>
      {notice_html}
      <div class="beta-action-row">
        <a class="beta-button beta-button--primary" href="{_AUTH_GITHUB_START_PATH}">
          Continue with GitHub
        </a>
        <p class="beta-inline-hint">
          After sign-in, the portal will generate ready-to-run Codex and Claude setup commands.
        </p>
      </div>
    </div>
    <aside class="beta-callout">
      <p class="beta-eyebrow">Hosted endpoint</p>
      <h2 class="beta-card-title">Provision a bearer token for the shared MCP route.</h2>
      <p class="beta-body-copy">
        The hosted beta issues one active API key per account. Rotate the key whenever you need
        to reconnect a client or invalidate an older secret.
      </p>
      <div class="beta-inline-code">{mcp_url_html}</div>
      <div class="beta-meta-list">
        <div class="beta-meta-row">
          <span class="beta-meta-label">Portal URL</span>
          <a href="{portal_url_html}">{portal_url_html}</a>
        </div>
        <div class="beta-meta-row">
          <span class="beta-meta-label">Flow</span>
          <strong>Sign in -&gt; generate key -&gt; connect client</strong>
        </div>
      </div>
    </aside>
  </section>

  <section class="beta-section">
    <div class="beta-section__header">
      <p class="beta-kicker">Quickstart</p>
      <h2 class="beta-section-title">Connect in three moves</h2>
    </div>
    <div class="beta-card-grid beta-card-grid--steps">
      <article class="beta-card beta-card--emphasis">
        <span class="beta-step-index">1</span>
        <h3 class="beta-card-title">Authenticate with GitHub</h3>
        <p class="beta-command-description">
          Start the hosted beta session from the GitHub OAuth flow. PolicyNIM stores the portal
          session and keeps the MCP endpoint locked behind bearer auth.
        </p>
      </article>
      <article class="beta-card">
        <span class="beta-step-index">2</span>
        <h3 class="beta-card-title">Issue or rotate your API key</h3>
        <p class="beta-command-description">
          The dashboard reveals the full secret once, along with the current key prefix and the
          latest issuance timestamp for quick recovery.
        </p>
      </article>
      <article class="beta-card">
        <span class="beta-step-index">3</span>
        <h3 class="beta-card-title">Paste the generated client commands</h3>
        <p class="beta-command-description">
          Once signed in, the portal shows ready-to-run commands for both Codex and Claude Code
          so the hosted MCP route is connected without any manual command assembly.
        </p>
      </article>
    </div>
    <p class="beta-grid-note beta-fineprint">
      Portal URL: <a href="{portal_url_html}">{portal_url_html}</a>
    </p>
  </section>
</main>
"""
    response = _render_beta_page(page_class="beta-page--landing", content=content)
    response.status_code = status_code
    return response


def _render_beta_dashboard(
    settings: Settings,
    *,
    account: BetaAccount,
    usage: BetaUsageSnapshot,
    new_api_key: str | None = None,
    message: str | None = None,
    message_tone: str = "warning",
) -> HTMLResponse:
    portal_url = _derive_beta_url(settings) or _BETA_PATH
    mcp_url = _derive_mcp_url(settings) or _STREAMABLE_HTTP_PATH
    current_key = account.api_key_prefix or "No active key"
    current_key_created = (
        account.api_key_created_at.isoformat() if account.api_key_created_at is not None else "N/A"
    )
    usage_text = f"{usage.request_count} / {usage.quota} requests, {usage.remaining} remaining."
    usage_percent = _beta_usage_percent(usage)
    status_class = f"beta-status-pill beta-status-pill--{account.status}"
    codex_command = (
        f"codex mcp add policynim --url {mcp_url} --bearer-token-env-var POLICYNIM_TOKEN"
    )
    claude_command = (
        "claude mcp add --transport http policynim "
        f'{mcp_url} --header "Authorization: Bearer $POLICYNIM_TOKEN"'
    )

    notice_parts: list[str] = []
    if account.status != "active":
        notice_parts.append(
            _render_beta_notice(
                title="Account suspended",
                message=("Existing API keys will be rejected until the account is resumed."),
                tone="warning",
            )
        )
    if message:
        notice_title = "Portal update" if message_tone == "success" else "Action required"
        notice_parts.append(
            _render_beta_notice(
                title=notice_title,
                message=message,
                tone=message_tone,
            )
        )
    notices_html = ""
    if notice_parts:
        notices_html = f'<div class="beta-notice-stack">{"".join(notice_parts)}</div>'

    new_key_html = ""
    if new_api_key is not None:
        export_command = f"export POLICYNIM_TOKEN={new_api_key}"
        new_key_html = f"""
  <section class="beta-section">
    <article class="beta-card beta-card--secret">
      <div class="beta-card__header">
        <div>
          <p class="beta-eyebrow">New API key</p>
          <h2 class="beta-card-title">Copy it now</h2>
        </div>
        <div class="beta-command-actions">
          <button
            type="button"
            class="beta-copy-button"
            data-copy="{html.escape(export_command, quote=True)}"
          >
            Copy export
          </button>
        </div>
      </div>
      <p class="beta-command-description">
        This secret is shown only once. Set <code>POLICYNIM_TOKEN</code> before connecting your
        client.
      </p>
      <pre class="beta-command-block">{html.escape(export_command)}</pre>
    </article>
  </section>
"""

    content = f"""
<main class="beta-shell beta-shell--dashboard">
  <div class="beta-shell__utility">
    {_render_beta_theme_toggle()}
  </div>
  <header class="beta-topbar">
    <div class="beta-brand">
      {_render_beta_logo(alt="PolicyNIM", compact=True)}
      <div class="beta-brand__copy">
        <p class="beta-kicker">Hosted MCP beta</p>
        <h1 class="beta-title">PolicyNIM Hosted Beta</h1>
      </div>
    </div>
    <div class="beta-command-actions">
      <span class="{status_class}">{html.escape(account.status.title())}</span>
      <p class="beta-inline-hint">
        Portal URL:
        <a href="{html.escape(portal_url, quote=True)}">{html.escape(portal_url)}</a>
      </p>
    </div>
  </header>

  {notices_html}

  <section class="beta-card-grid beta-card-grid--dashboard">
    <article class="beta-card beta-card--emphasis">
      <div class="beta-card__header">
        <div>
          <p class="beta-eyebrow">Account</p>
          <h2 class="beta-card-title">Signed in as {html.escape(account.github_login)}</h2>
        </div>
        <span class="{status_class}">{html.escape(account.status.title())}</span>
      </div>
      <dl class="beta-facts">
        <div class="beta-facts__row">
          <dt>GitHub</dt>
          <dd>{html.escape(account.github_login)}</dd>
        </div>
        <div class="beta-facts__row">
          <dt>Email</dt>
          <dd>{html.escape(account.email or "Not available")}</dd>
        </div>
        <div class="beta-facts__row">
          <dt>Status</dt>
          <dd>{html.escape(account.status)}</dd>
        </div>
        <div class="beta-facts__row">
          <dt>Active key prefix</dt>
          <dd>{html.escape(current_key)}</dd>
        </div>
        <div class="beta-facts__row">
          <dt>Key created at</dt>
          <dd>{html.escape(current_key_created)}</dd>
        </div>
      </dl>
      <div class="beta-usage">
        <div class="beta-usage__row">
          <span class="beta-meta-label">UTC-day usage</span>
          <strong>{html.escape(usage_text)}</strong>
        </div>
        <div class="beta-usage__bar" aria-hidden="true">
          <span style="width:{usage_percent}%"></span>
        </div>
        <p class="beta-fineprint">
          Usage snapshot date: {html.escape(usage.usage_date.isoformat())} UTC.
        </p>
      </div>
    </article>

    <article class="beta-card">
      <div class="beta-card__header">
        <div>
          <p class="beta-eyebrow">API key</p>
          <h2 class="beta-card-title">Generate, rotate, and recover access</h2>
        </div>
      </div>
      <p class="beta-command-description">
        Generate a fresh API key whenever you reconnect a client. A new key replaces the previous
        secret immediately, so copy the export command before closing the page.
      </p>
      <div class="beta-button-row">
        <form method="post" action="{_BETA_API_KEY_REGENERATE_PATH}">
          <button type="submit" class="beta-button beta-button--primary">
            Generate or Rotate API Key
          </button>
        </form>
        <form method="post" action="{_BETA_LOGOUT_PATH}">
          <button type="submit" class="beta-button beta-button--secondary">Sign Out</button>
        </form>
      </div>
      <div class="beta-meta-list">
        <div class="beta-meta-row">
          <span class="beta-meta-label">Hosted MCP endpoint</span>
          <div class="beta-inline-code">{html.escape(mcp_url)}</div>
        </div>
      </div>
    </article>
  </section>

  {new_key_html}

  <section class="beta-section">
    <div class="beta-section__header">
      <p class="beta-kicker">Client setup</p>
      <h2 class="beta-section-title">Copy the exact commands for your client</h2>
    </div>
    <div class="beta-card-grid beta-card-grid--commands">
      {
        _render_copyable_command(
            title="Connect Codex",
            description=(
                "Add the hosted PolicyNIM MCP endpoint to Codex using the generated bearer token "
                "environment variable."
            ),
            command=codex_command,
        )
    }
      {
        _render_copyable_command(
            title="Connect Claude Code",
            description=(
                "Register the same hosted MCP endpoint in Claude Code and pass the bearer token "
                "through the Authorization header."
            ),
            command=claude_command,
        )
    }
    </div>
  </section>
</main>
"""
    return _render_beta_page(page_class="beta-page--dashboard", content=content)


def _client_address(request: Request, *, trust_forwarded_headers: bool = False) -> str:
    if trust_forwarded_headers:
        forwarded_ip = _forwarded_client_address(request.headers)
        if forwarded_ip is not None:
            return forwarded_ip

    client = request.client
    if client is None or not client.host:
        return "unknown"
    return client.host


def _forwarded_client_address(headers: Headers) -> str | None:
    x_forwarded_for = headers.get("x-forwarded-for")
    if x_forwarded_for is not None:
        for candidate in x_forwarded_for.split(","):
            forwarded_ip = candidate.strip()
            if forwarded_ip:
                return forwarded_ip

    forwarded = headers.get("forwarded")
    if forwarded is None:
        return None

    for forwarded_value in forwarded.split(","):
        for attribute in forwarded_value.split(";"):
            key, separator, value = attribute.partition("=")
            if separator and key.strip().lower() == "for":
                forwarded_ip = value.strip().strip('"')
                if forwarded_ip.startswith("[") and "]" in forwarded_ip:
                    return forwarded_ip[1 : forwarded_ip.index("]")]
                if forwarded_ip:
                    return forwarded_ip.removeprefix("for=").split(":")[0]
    return None


def _require_beta_session_account_id(request: Request) -> int | None:
    raw_value = request.session.get(_BETA_ACCOUNT_SESSION_KEY)
    try:
        if raw_value is None:
            return None
        return int(raw_value)
    except (TypeError, ValueError):
        return None


def _beta_session_https_only(settings: Settings) -> bool:
    """Use secure beta session cookies for HTTPS-hosted deployments."""
    if settings.mcp_public_base_url is None:
        return False
    return settings.mcp_public_base_url.scheme == "https"


def _build_beta_auth_service(settings: Settings) -> BetaAuthService | None:
    if not settings.beta_signup_enabled and not settings.mcp_require_auth:
        return None
    try:
        return create_beta_auth_service(settings)
    except Exception as exc:
        if settings.beta_signup_enabled or not settings.mcp_bearer_tokens:
            raise ConfigurationError(
                "Hosted beta auth initialization failed. Check "
                "`POLICYNIM_BETA_AUTH_DB_PATH`, GitHub OAuth settings, "
                "and the writable auth volume."
            ) from exc
        LOGGER.exception(
            "Hosted beta auth initialization failed. "
            "Continuing with env-issued break-glass tokens only."
        )
        return None


class _BearerProtectedASGIApp:
    """Protect the MCP HTTP route with exact-match bearer token auth."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        protected_path: str,
        valid_tokens: list[str],
        beta_auth_service: BetaAuthService | None,
    ) -> None:
        self._app = app
        self._protected_path = protected_path
        self._valid_tokens = set(valid_tokens)
        self._beta_auth_service = beta_auth_service

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path") != self._protected_path:
            await self._app(scope, receive, send)
            return

        token = _extract_bearer_token(scope)
        auth_result: str | None = None
        response: JSONResponse | None = None
        if token is not None and token in self._valid_tokens:
            auth_result = "authorized"
        elif self._beta_auth_service is not None:
            decision = self._beta_auth_service.authenticate_api_key(token=token)
            if decision.status == "authorized":
                auth_result = "authorized"
            elif decision.status == "suspended":
                auth_result = "suspended"
                response = JSONResponse({"error": "Account suspended."}, status_code=403)
            elif decision.status == "quota_exceeded":
                auth_result = "quota_exceeded"
                response = JSONResponse({"error": "Quota exceeded."}, status_code=429)
            else:
                auth_result = "unauthorized"
                response = JSONResponse({"error": "Unauthorized."}, status_code=401)
        else:
            auth_result = "unauthorized"
            response = JSONResponse({"error": "Unauthorized."}, status_code=401)

        if auth_result != "authorized":
            _emit_hosted_event(
                "mcp.auth",
                auth_result=auth_result or "unauthorized",
                tool_name=None,
                latency_ms=None,
                upstream_failure_class=None,
                request_id=None,
            )
            assert response is not None
            await response(scope, receive, send)
            return

        token_state = _HOSTED_AUTH_RESULT.set(auth_result)
        try:
            await self._app(scope, receive, send)
        finally:
            _HOSTED_AUTH_RESULT.reset(token_state)


def _extract_bearer_token(scope: Scope) -> str | None:
    """Return the bearer token from the HTTP Authorization header, if valid."""
    headers = Headers(scope=scope)
    authorization = headers.get("authorization")
    if authorization is None:
        return None

    parts = authorization.strip().split()
    if len(parts) != 2:
        return None
    scheme, token = parts
    if scheme.lower() != "bearer":
        return None
    return token.strip() or None


def _build_streamable_http_app(settings: Settings) -> ASGIApp:
    """Create the streamable-http ASGI app, wrapping auth only when required."""
    beta_auth_service = _build_beta_auth_service(settings)
    server = _create_mcp_server(settings, beta_auth_service=beta_auth_service)
    app = server.streamable_http_app()
    if settings.beta_signup_enabled:
        assert settings.beta_session_secret is not None
        app = SessionMiddleware(
            app,
            secret_key=settings.beta_session_secret,
            same_site="lax",
            https_only=_beta_session_https_only(settings),
        )
    if not settings.mcp_require_auth:
        return app
    return _BearerProtectedASGIApp(
        app,
        protected_path=server.settings.streamable_http_path,
        valid_tokens=settings.mcp_bearer_tokens,
        beta_auth_service=beta_auth_service,
    )


def _run_streamable_http_app(
    app: ASGIApp,
    *,
    host: str,
    port: int,
    log_level: str = "info",
) -> None:
    """Serve the hosted HTTP app through uvicorn."""
    import uvicorn

    config = uvicorn.Config(app, host=host, port=port, log_level=log_level)
    server = uvicorn.Server(config)
    server.run()


def run_server(transport: str = "stdio") -> None:
    """Run the PolicyNIM MCP server."""
    if transport not in SUPPORTED_TRANSPORTS:
        allowed = ", ".join(SUPPORTED_TRANSPORTS)
        raise ValueError(f"Transport must be one of: {allowed}.")

    settings = get_settings()
    if transport == "streamable-http":
        _configure_hosted_logger()
        _ensure_streamable_http_port_available(settings.mcp_host, settings.mcp_port)
        ensure_hosted_runtime_ready(settings, rebuild_if_missing=True)
        app = _build_streamable_http_app(settings)
        try:
            _run_streamable_http_app(app, host=settings.mcp_host, port=settings.mcp_port)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise ConfigurationError(
                    _streamable_http_port_in_use_message(settings.mcp_host, settings.mcp_port)
                ) from exc
            raise
        return

    server = _create_mcp_server(settings)
    server.run(transport=transport)


mcp = _register_tools(FastMCP("PolicyNIM", json_response=True))


if __name__ == "__main__":
    run_server()
