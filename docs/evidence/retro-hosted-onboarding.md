# Hosted onboarding browser checks

The hosted portal takes a developer from GitHub sign-in to a one-time API key and the command for their coding agent. We exercised the [November 4 design's acceptance criteria](../build-plans/retro-hosted-onboarding.md) through the real HTTP routes, session cookies, key-generation forms and SQLite transactions.

## Reproduce the browser run

1. From the repository root, run `uv sync --locked --group test --group dev`.
2. Run `uv run --no-sync python docs/evidence/retro-hosted-onboarding/serve.py`. Open the printed loopback URL in a fresh browser profile. The fixture uses its assigned port for both portal links and client commands.
3. Sign in, generate a key and try Next before and after checking **I've saved my key**. Use the Codex and Claude Code tabs, copy a command or prompt, then choose Finish. On a second generation, reload the POST response and check the active prefix. Stop the fixture with Ctrl+C to remove its temporary database and index.

The fixture supplies a synthetic GitHub code and identity. The application signs the session, creates the account, rotates keys and serves the packaged pages. We confined clipboard tests to a browser-local sink, including a rejected write. Browser and fixture output excluded keys, cookie values and form IDs. The flow ran entirely on loopback.

For the reflow checks, use a 1280 by 900 window at 400% browser zoom. Separately, reset zoom to 100%, set the base browser font to 32px and repeat at 320px wide.

## Browser results

Product revision `0a8dbc21fa67eb779a36c77e562e8c259c77e0c0`. Playwright CLI 0.1.25 drove Chrome 157.0.8103.80 on macOS with Python 3.11.13. The fixture used HTTP loopback. HTTPS cookie flags also have separate route tests.

| Path | Observed result |
| --- | --- |
| Sign-in and recovery | GitHub denial and missing state rendered 400. The mock exchange failure rendered 502. Each page retained its sign-in action. Sign-in returned to the account. Logout and removing the session returned to the welcome screen. The HTTP-only session cookie used SameSite=Lax. Account HTML included both no-store and no-cache headers. |
| One-time key | The POST displayed the export and focused the key heading. The checkbox enabled Next. Back opened the account, and Next opened the instructions. Both responses omitted the secret. Reloading a successful POST or replaying its form returned 303 and preserved the prefix. An explicit rotation issued a different key. A malformed form returned 400, and its recovery link opened a fresh form. |
| Instructions and clipboard | Arrow keys, Home and End selected the expected client and moved focus. Each copied command matched the server-rendered bytes, including the configured host. The first prompt copied in full. A rejected write left selectable text and manual-copy instructions. Successful copies announced only their status. Finish returned to the account heading. Wizard navigation made zero generation requests. Neither issued key appeared in the observed request URLs, cookies or web storage. |
| Reflow and keyboard | All three screens fit at 320 CSS pixels. In a dedicated Chrome profile, native 400% page zoom changed a 1280 by 900 viewport to 320 by 225 CSS pixels. Doubling the browser's base font size to 32px at 320px width kept the controls and long account email within the page. We scrolled to act on below-the-fold controls. Keyboard navigation showed focus, and Skip to setup preserved the selected instructions. |
| Preferences and fallback | Reduced motion disabled the entrance animation. The system dark theme applied on first load, an explicit light choice survived reload, and blocked storage still allowed sign-in and theme changes. Sampled text/background pairs across all three screens had minimum contrast ratios of 7.12 to 1 in light mode and 7.74 to 1 in dark mode. With JavaScript disabled, sign-in, key generation, the required checkbox, both selectable client commands and logout worked. |

## Captures

The links point to the immutable screenshot commit. Local copies live under `output/playwright/retro-hosted-onboarding/`. The desktop images use the mockups' 1586 by 992 dimensions. The narrow image shows the full instructions at 320px wide. We replaced the disposable key with a label for the key screenshot.

[Welcome](https://github.com/nnennandukwe/policyNIM/blob/70d9ec9d2a2dd3447b2ecc0e3285a5dfa04a26ad/docs/evidence/retro-hosted-onboarding/01-landing.png) / [Save your API key](https://github.com/nnennandukwe/policyNIM/blob/70d9ec9d2a2dd3447b2ecc0e3285a5dfa04a26ad/docs/evidence/retro-hosted-onboarding/02-api-key.png) / [Connect your agent](https://github.com/nnennandukwe/policyNIM/blob/70d9ec9d2a2dd3447b2ecc0e3285a5dfa04a26ad/docs/evidence/retro-hosted-onboarding/03-connect.png) / [320px instructions](https://github.com/nnennandukwe/policyNIM/blob/70d9ec9d2a2dd3447b2ecc0e3285a5dfa04a26ad/docs/evidence/retro-hosted-onboarding/04-connect-narrow.png)

## Package and route checks

On product commit `0a8dbc21fa67eb779a36c77e562e8c259c77e0c0`, all 236
focused tests in the build plan passed. Ruff, its formatting check, Pyright and
`uv lock --check` passed. We built the source distribution and wheel from an
archive of that commit with `uv build --python 3.11`. All 13 portal templates
and assets matched byte for byte across source, both archives and a fresh wheel
install. From outside the checkout, the installed portal served the welcome
page and its CSS and scripts with HTTP 200. The repository's
`scripts/verify_installed_package.py` also passed on that wheel under macOS
and Python 3.11.13 using the locked verification dependencies.

The initial offline runs on Python 3.11 and 3.12 each reached two installer
tests that require PowerShell. The Mac's Homebrew PowerShell 7.6.0 process
crashed at startup with `System.IO.FileLoadException`, including when asked
only for its version. Both installer cases passed with a temporary copy of
PowerShell 7.4.13. We verified the arm64 archive against its published SHA-256
digest and placed that copy first on the test process's PATH for the full
reruns. The delivery PR lists the final results. [PowerShell release](https://github.com/PowerShell/PowerShell/releases/tag/v7.4.13)

## Review and delivery

The fast local Qodo checkpoint covered the feature at `499aa01` against
`2a58a51`. Review `9282a9a0-5afc-4139-ae2e-6f212d96da43` returned two
informational findings. The light logo still supplies the favicon, and the
published light and dark image URLs remain covered by route tests
(`0d44f5b3-5e52-444d-af5a-2c30b0478f95`). Chrome focused the fresh-key
heading on the POST response, including with JavaScript disabled
(`3ac87c07-9dbc-4582-92c5-e18690f856b4`). A separate browser observation
on Next led to the heading-target fix. The checkpoint reported complete
coverage for its scope and skipped its spec pass. Neither finding was
manually closed.

The full local review of evidence commit `70d9ec9` returned zero findings
and marked coverage incomplete. Its skipped-file list contained the five PNG
captures. We inspected all five and linked them by commit, so the final diff
includes the report, fixture and README preview link. The screenshots remain
available at the revision where they were first recorded.

Use the delivery PR's verification section for the final local and hosted
review revisions, the merge commit, post-merge CI and the deployment result.
[Find the delivery PR](https://github.com/nnennandukwe/policyNIM/pulls?q=is%3Apr+head%3Acodex%2Fretro-hosted-onboarding)
