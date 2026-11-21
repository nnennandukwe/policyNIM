# PolicyNIM Retro Hosted Onboarding Build Plan

## Summary

- Outcome: implement the three saved Windows 95 / terminal mockups as PolicyNIM's hosted onboarding interface.
- Authority: Nnenna's request in this chat to turn the November 4 mockups into a Software Build Plan and start an implementation Goal. Visual sources are 01-landing.png, 02-api-key-wizard.png, 03-connect-agent.png and design-notes.txt in /Users/nnennandukwe/.codex/visualizations/2026/11/04/01a1a89b-68dc-7e32-8e49-6748627f0a8b/.
- Starting baseline: origin/main at 2a58a51e409271d146afdd72b6ac6065135ed71c, refreshed November 20. Recheck before execution.
- Review unit: one coherent PR containing the plan, hosted presentation changes, regression coverage and sanitized browser evidence.
- Primary boundary: the hosted /beta portal and its existing auth/key/agent-instruction contracts. Existing application behavior supplies the source of truth for commands and account state.

## Skills To Use

| Execution stage | Skill | Responsibility and evidence |
|---|---|---|
| Context and rules | qodo-get-rules, qodo-codebase-wisdom | Apply the retrieved PolicyNIM rules and current source contracts before implementation. Retain rule IDs and provenance in review context. |
| Planning and visual implementation | software-build-plan, frontend-skill | Save this canonical plan before coding. Use the three approved mockups for composition, hierarchy, controls and responsive acceptance. |
| Failure and recovery coverage | failure-path-testing | Exercise blocked auth, key-generation failure/replay, refresh and clipboard recovery through actual server/browser boundaries. |
| Browser acceptance | playwright | Use the installed CLI/wrapper for real HTML/CSS/JS checks, keyboard paths and sanitized captures. Record fixture boundaries and exact head. |
| Review and delivery | qodo-review, qodo-review-resolver | Review the full committed change set locally, assess findings, and verify completed hosted review at the PR head before merge. |
| Written material | voice-canon | Check UI copy, plan, evidence and final prose with the current required-language rules and verify_language.py. |

Qodo Local Review eligibility: Git repository with a pushed origin/main base and successful managed repository reads. Confirm review entitlement/access during preflight; a runtime failure isn't an eligibility skip. No review has run for this new build yet.

Retrieved standards: ERROR 2601012/2601009 keep HTTP validation in handlers, business behavior in services and storage operations in repositories. ERROR 1060853/1060879/1060888 preserve consumers of fields, routes, configuration and flags. Their generic compatibility requirements apply without importing the originating project's workflow. WARNING 576985 requires ASCII strings/documentation unless an explicit exception applies; use CSS/SVG for iconography. WARNING 784431 requires docstrings for new/modified Python functions; naming warnings 8184/7205/699152/613754 govern readable English identifiers/comments. Conditional schema rules remain relevant only if the scope legitimately reaches schemas. Retrieval IDs: 8f2fa01b160ca56387113a3c8b492234 and 33a4419a7a3149cabf7865edf3e0386d; source query 57d145607e81c533006d8e5236ab9090. Rule URLs weren't supplied. Read access doesn't establish review completion.

Coverage notes: current Jinja2, CSS and vanilla JavaScript fit the existing stack. Dedicated architecture, schema-migration, SQLite-concurrency and service-lifecycle skills aren't needed unless a supported defect expands those surfaces. The frontend skill's generic photography and frameless-hero defaults yield to the user's approved desktop-window designs. No new visual concept or UI framework is required.

## Scope

In scope:
- Rebuild the landing, fresh-key and agent-instruction screens with teal desktop, silver beveled window, navy chrome/progress rail, green terminal, pixel-style icons and taskbar.
- Define useful Back/Next/Finish navigation, saved-key acknowledgement, Codex/Claude tabs, copy feedback and recovery using existing server facts.
- Retain returning-user key/account/usage/suspension information, links and supported theme behavior.
- Add responsive, keyboard, readable-text and reduced-motion behavior and verify it in a browser.
- Deliver through tests, review, PR, gated merge, post-merge checks and a compact evidence record.

Out of scope:
- New auth systems, MCP tools, connection-testing services, schema/storage changes or product features.
- The separate local policynim setup UI, native Tasks #102, unrelated dependency upgrades and reopening completed roadmap #88.
- A draggable desktop, working operating-system menus/window manager, new animation library, live provider calls or changes to real user credentials for verification.

## Package Layout / File-to-Task Mapping

Paths below are relative to the implementation worktree, which must start from current origin/main. The original checkout is older and has unrelated untracked work.

| Path | Responsibility |
|---|---|
| docs/build-plans/retro-hosted-onboarding.md (new) | Track this plan and its visual/behavior acceptance criteria. |
| src/policynim/templates/beta/page.html.j2 and partials/logo.html.j2, notice.html.j2, theme_toggle.html.j2 | Shared desktop/window structure, navigation, notices and preference controls. Add a shared partial only if repeated markup warrants it. |
| src/policynim/templates/beta/landing.html.j2 | Signed-out welcome composition and existing GitHub/docs/source actions. |
| src/policynim/templates/beta/dashboard.html.j2 and partials/new_key_panel.html.j2, command_card.html.j2 | Fresh-key and agent-setup views plus returning-user/account information. |
| src/policynim/assets/beta/beta.css, beta-page.js, beta-theme-init.js | Retro design tokens, responsive layout, accessible interactions, copy feedback and existing theme behavior. Add small local SVG assets only when needed. |
| src/policynim/interfaces/mcp.py | Minimal template-context adaptation only if required; retain routes and business logic ownership. |
| tests/test_beta_portal.py, test_beta_key_submissions.py, test_beta_auth.py | HTTP/template/security and key-lifecycle regression coverage. Preserve existing tests in test_mcp.py, test_mcp_authorization.py, test_docs_hosted_onboarding.py and test_package_release.py. |
| docs/evidence/retro-hosted-onboarding.md (new), output/playwright/ | Record commands, fixture boundaries, exact revisions, sanitized captures and observed outcomes. Keep transient browser output local according to repository ignore rules. |

## Dependencies And Settings

- Runtime dependencies: retain Starlette/Jinja2, existing CSS/JS and packaged assets. Add no framework, hosted fonts, CDN requests or runtime packages.
- Development dependencies: use the existing Python test/tool groups and installed Playwright CLI/wrapper. Don't introduce a browser test framework solely for screenshots.
- Schema or storage version: unchanged.
- Environment/configuration: retain current beta settings, trusted public-host derivation and theme precedence. Introduce no environment variable.
- Toolchain: Python 3.11/3.12 and locked uv dependencies. npx is available for the installed Playwright wrapper. Review packaged wheel/sdist asset inclusion.

## Canonical Contracts

Preserve:
- GET /beta selects landing or signed-in dashboard. GET /auth/github/start and /auth/github/callback, POST /beta/api-key/regenerate, POST /beta/logout and /beta/assets/* retain their methods and behavior. Hosted / isn't a newly defined landing route.
- GitHub state/session validation, account ownership, SameSite=Lax, HTTPS-secure cookies, logout and the separation between portal sign-in and MCP OAuth.
- A fresh account-bound signed submission expires after 30 minutes and rotates a key atomically. Replay returns 303 without rotation; overlap returns 409; invalid/expired forms return 400.
- Plaintext keys appear only in the successful POST response. A refresh returns prefix/time, and a lost response requires an explicit fresh generation. Back/Next/Finish never issue key-generation requests. Secrets stay out of URLs, cookies, storage, announcements and logs.
- Current server-generated Codex/Claude instructions, host values, account/usage facts, reserved work units and warnings. Reuse actual supported commands and variables rather than copying demo commands from PNGs.
- Jinja escaping and HTML no-store/no-cache headers. The hosted app has no explicit CSP at this baseline; don't claim the separate local setup policy applies here.

Setup state:
- Signed-out users see the welcome view and GitHub action.
- Authenticated users see existing key metadata or an explicit generation action. A successful generation displays the one-time key view.
- "I've saved my key" is local acknowledgement. It gates a user-driven Next action in that fresh-key view, without persisting the secret or inventing server evidence.
- Returning users can open connection instructions without generating a new key. A failed or replayed submission keeps server-owned notices and recovery actions visible.
- Codex/Claude tabs select instructions only. Finish completes the instruction flow or returns to the account view. It never labels an agent connected or a policy check successful without runtime evidence.
- Keep a usable server-rendered path if JavaScript fails; essential auth, generation and instructions remain reachable.

Visual contract:
- Use the approved palette as the default reference: desktop #0B5552, silver #C4C4BA, navy #142747, terminal #0A1410, green #A8F66B. Preserve readable supported theme choices.
- Use square bevels, hard offset shadows, at most two font families and selectable text. No screenshot serves as the functional interface.
- Desktop welcome matches the CTA/terminal composition; steps two/three share the progress rail and working pane.
- At 320 CSS pixels, stack panes and compact the step rail; allow vertical flow, wrap visible commands while copying unchanged bytes, and keep controls clear of the taskbar.
- Give real links/buttons/tabs/checkboxes native semantics, visible focus and useful labels. Tabs support keyboard navigation and selected state. Step changes move focus to their heading.
- File/View/Help, Start and window-control artwork stay nonfocusable decoration unless a defined existing action supports them.
- Use dark text on silver, green on dark surfaces, and dark text on selected green. Check contrast, enlarged text, 400% zoom and reduced motion. Keep instructions immediate.

## TDD And BDD Implementation Strategy

Test seams: existing ASGI route/form/storage fixtures plus real browser execution of packaged HTML, CSS and JavaScript.

1. Capture the unchanged hosted contract and add meaningful failing assertions for the welcome/actions and accessible step structure. Implement shared chrome and landing.
2. Exercise fresh generation, replay, refresh and returning-user paths. Implement the key view/navigation while proving that UI navigation never rotates or restores a secret.
3. Exercise both client tabs, copy success/failure and no-JS fallback. Implement connection instructions and truthful Finish behavior.
4. Check all three screens at desktop/mobile sizes with keyboard and reduced motion. Repair observed visual or interaction gaps, then run full regression and package checks.

Use browser observations for composition and interaction; don't replace behavior proof with CSS-string tests or unit tests that mirror markup.

## Component Design

The existing server owns account/session/key/usage state. Jinja templates render those facts and supported commands. Shared CSS owns retro tokens/layout; the existing page script owns temporary view/tab/acknowledgement/copy state and existing theme preferences. Auth/key services and the atomic storage transaction remain their current owners.

Keep the fresh secret in the current response only. Use data attributes and event listeners within the existing packaged script pattern. Keep error notices visible during navigation. Visual decoration doesn't create hidden application actions. Use brief entrance/pressed/tab feedback with a static reduced-motion fallback, without a new library.

## Failure And Recovery Rules

- Sign-in denial, invalid state, provider failure or expired session retains its current failure response and useful retry/sign-in route; the wizard can't advance as authenticated.
- Invalid/expired submissions and overlap retain 400/409 behavior and visible notices. Replay remains 303 and doesn't rotate. Recovery is an explicit fresh generation, including after a lost one-time response.
- Clipboard rejection leaves selectable text and a short accessible failure message. Copy success is announced without reading the secret into a status region.
- Theme/storage failure leaves a usable page. Missing JavaScript leaves essential forms/instructions available. Narrow screens, long values and zoom can't hide actions or trap focus.
- Capture failures with disposable fixture identities and redacted artifacts. Teardown removes only this build's temporary data/processes.

## Commit Plan

1. docs(beta): record retro onboarding plan and acceptance contracts.
2. feat(beta): add retro desktop shell and welcome flow with behavior checks.
3. feat(beta): implement key and agent setup views with recovery/regression coverage.
4. test(beta): finish browser/package verification and update affected onboarding evidence.

Keep fixes with the behavior they prove; combine commits when that gives a clearer review unit.

## Branch And PR Flow

1. Fetch origin/main, inspect worktree ownership and create an isolated implementation worktree. Proposed branch: codex/retro-hosted-onboarding. Preserve the original checkout's AGENTS.md, .agents/ and docs/research/. Re-read applicable instructions in the new checkout.
2. Save this plan as a tracked file before product edits. Complete the vertical slices and relevant checks. Commit only task-owned changes before each Qodo checkpoint/final review.
3. Pin the pushed merge-base and attach source design requirements, session decisions, actual verification and recent_work to Qodo. Use fast async checkpoints for completed slices and auto depth for final full-diff review. Reproduce findings, fix supported actionable issues, rerun focused/full checks and review the final changed head again.
4. Open one PR after the local review gate, linking the tracked plan and this user-authorized design scope. Require completed hosted Qodo review at the PR head, relevant required CI and supported actionable findings addressed. Prefer automatic fix re-attribution. Stored dismissals still require the user's authorization for each finding. Undocumented review_complete metadata isn't an extra gate.
5. Recheck exact head, safely merge with the reviewed SHA, compare the merged tree, and verify post-merge CI. Normal automatic repository PR previews/production deployment retain the user's earlier authorization from chat 01a1d851-8edc-77d0-bc58-75440f1be4bb. Observe deployed rendering if that pipeline runs; keep source/CI/browser/deployment results separately evidenced.

Eligible review runtime/auth/transport failures follow qodo-review's bounded recovery and remain visible. No manual gate bypass or unrelated release/publication is authorized.

## Test Plan

Focused:
```bash
uv sync --locked --group test --group dev
uv run --no-sync pytest -q -m "not live and not docker_live" tests/test_beta_portal.py tests/test_beta_key_submissions.py tests/test_beta_auth.py tests/test_mcp.py tests/test_mcp_authorization.py tests/test_docs_hosted_onboarding.py tests/test_package_release.py
uv run --no-sync ruff check
uv run --no-sync ruff format --check
uv run --no-sync pyright
uv lock --check
git diff --check
```

Run the full offline pytest suite without file arguments on Python 3.11 and 3.12, matching CI. Use established CI for any required matrix that can't run locally and record that limit. Follow test_package_release.py's existing installed-package checks for template/static asset availability.

Browser acceptance will use an isolated loopback app with disposable SQLite/account/index fixtures and a mocked GitHub exchange, while retaining actual route/session/form/storage behavior. Document the reproducible fixture/server invocation. Exercise welcome, callback, dashboard, fresh key, refresh/replay, deliberate rotation, logout, both client tabs, no-JS path, copy failure, keyboard, reduced motion, zoom and long values. Capture all three reference views plus a narrow viewport. Mark mocked provider boundaries explicitly; browser setup alone isn't a live client connection receipt.

Run focused/full checks again after review repairs and bind Qodo's final local and hosted results to their actual revisions.

## Definition Of Done

- All three hosted views faithfully implement the saved design with real usable controls and readable mobile/keyboard/reduced-motion behavior.
- Auth, one-time key, replay/rotation, account/usage and generated-command contracts pass their regression checks; setup wording follows actual evidence.
- Required Python, package, browser and CI verification passes, with any unavailable observation explicitly recorded.
- The final complete local diff and current PR head meet documented Qodo gates, the reviewed tree is merged, and post-merge CI passes.
- The tracked plan and sanitized evidence identify source designs, tested/reviewed/merged revisions, commands, outcomes and fixture/deployment limits. The Goal is complete only after these mandatory outcomes are achieved.

## Assumptions And Defaults

Nnenna's current instruction authorizes this design's implementation Goal; the November mockup-only boundary has been superseded for this build. Normal implementation, scoped verification/review repairs, commits, pushes, PR and gated merge can proceed without another routine continuation question.

The default is a faithful hosted presentation change on the current stack, one coherent PR and existing command/auth contracts. Routine layout/semantic choices can be resolved from the mockups and current source. A genuinely new product behavior, compatibility change, manual real-account credential mutation or infrastructure expense needs a separate decision. Keep optional Tasks and other projects outside this Goal.
