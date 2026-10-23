# Habit Hooks research for PolicyNIM

Research date: October 22, 2026. This examines the **Habit Hooks developer tool** at [habit-hooks.com](https://habit-hooks.com/). It's research and a proposed evaluation; no installation, integration, or benchmark run was performed.

## Recommendation

Evaluate Habit Hooks as an optional development-time coaching adapter around PolicyNIM's existing checks. Its most useful product idea for PolicyNIM is an explicit feedback loop: a check detects a specific failure, the system explains the governing rule and a useful correction, the agent revises, and the check runs again. PolicyNIM can apply that pattern to its policy-specific failures while retaining its own policy evidence and authorization rules. This is a proposed integration.

## What the product does

Habit Hooks is an MIT-licensed Python CLI for code-quality coaching. PyPI reported version `1.5.0` with Python `>=3.11` when checked. The source snapshot inspected was `5603331f8c31b1537761e6639f49b876acf0a4e4` on its main branch. [Package metadata](https://pypi.org/project/habit-hooks/1.5.0/), [source package configuration](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/pyproject.toml).

Its pipeline runs configured detectors, converts their findings into shared smell names, and maps those names to coaching guides. `habit-sensors` emits findings; `habit-mapper` groups and renders them. The architecture separates detection from the wording of the advice. [Architecture](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/docs/architecture.md).

The Python plugin invokes Ruff with `C901`, `PLR0913`, `PLR0915`, `F841`, `F401`, and `BLE001`, plus dependency and comment sensors. The generic plugin supplies language-independent checks and guides. PolicyNIM's current Ruff selection is `E`, `F`, `I`, and `UP`, so the two configurations overlap on unused imports and variables while the proposed addition would introduce structural checks. Thresholds and resulting refactors need review against PolicyNIM's existing design. [Python plugin configuration](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/plugins/python/src/habit_hooks_python/config.toml), [PolicyNIM package configuration at reviewed origin/main](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/pyproject.toml).

## Extensibility worth studying

Each finding carries a smell key, optional language, shared details, and occurrences identified by keys with per-occurrence details. That's a small interchange shape that a PolicyNIM adapter could produce. [Finding contract](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/docs/sensor-interface.spec.md).

External packages can contribute plugins through the `habit_hooks.plugins` entry-point group. A plugin packages its configuration, sensors, optional coaching guides, and approved scenarios. Guides use Jinja2 templates. Project configuration selects plugins and can tune severity or replace guides. These are documented extension points; a PolicyNIM plugin hasn't been built. [Plugin authoring](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/docs/authoring-plugins.spec.md), [configuration](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/docs/config.md).

## Where it fits PolicyNIM

PolicyNIM's conformance implementation checks preflight artifacts, including citation support and fields whose names refer to constraints, plan steps, and test expectations. Current default compile/preflight output quotes complete selected policy bodies and leaves advisory fields empty; those legacy field names shouldn't be presented as synthesized implementation advice. Habit Hooks' existing detectors operate on source-code smells. The useful connection is to make feedback from both surfaces actionable and traceable to the version checked. [PolicyNIM conformance implementation](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/src/policynim/services/conformance.py), [current output semantics](https://github.com/nnennandukwe/policyNIM/blob/0956cca74a7d83091839a03b0d4d1838d54b825f/docs/source-extractive-guidance.md).

Proposed sequence:

1. Pilot the Python and generic plugins in a disposable checkout as an advisory development check. Preserve existing Ruff, Pyright, tests, and review gates.
2. Inspect the findings and guides for useful advice, false positives, and incentives to move complexity into wrappers or parameter bags.
3. Prototype one PolicyNIM-owned adapter for a deterministic failure, such as missing citation support. Carry policy ID, revision, evidence IDs, failure category, and a bounded correction through the adapter.
4. Rerun the original check after each revision and retain the failure/revision/recheck relationship. Apply a retry limit and return an unresolved result when evidence stays insufficient.
5. Consider enforcement only after the pilot shows useful findings and acceptable interruption cost.

## Authority issue in the supplied agent instructions

The README recommends treating Habit Hooks output as a user prompt at the highest priority. That advice shouldn't be copied into PolicyNIM's agent instructions. Detector output, filenames, and coaching templates remain tool-provided data; they can't grant authorization or override the actual user and system instructions. [Agent integration section](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/README.md#agent-integration).

A suitable PolicyNIM rule would require reviewing findings within the authorized task, verifying supported fixes, and reporting unresolved failures. Permission to suppress findings or execute plugin scripts should remain governed by the user's task and the project's trusted configuration.

## Evidence behind its claims

The linked evaluation reports 18 hand-written Python functions covering excessive complexity and broad exception handling, with five trials per function per condition on Haiku 4.5 and Sonnet 4.6. The author reports higher mechanically scored genuine-fix rates with coaching prompts. The exact prompt came from the author's examiner hook. It's a prompt-framing experiment with known smells. [Evaluation repository](https://github.com/LiinaSuoniemi/prompt-vs-metric-eval).

The author documents a confound: the exception-handling prompt asks for the behavior checked by the judge. The judge also doesn't execute the revised programs. The complexity results vary by model. These limits make a PolicyNIM pilot necessary before claiming better correctness, fewer tokens, or improved performance across tasks. Results above are author-reported and haven't been reproduced here. [Evaluation methods, per-smell analysis, and judge limits](https://github.com/LiinaSuoniemi/prompt-vs-metric-eval#how-to-game-past-my-judge-the-honest-part).

## Proposed pilot measurements

Use the same tasks and model settings for bare findings versus findings with policy-grounded coaching. Retain the exact code and policy revision for every run. Measure:

- Behavior preservation through relevant tests and independent review of the changed code.
- Supported findings resolved, unresolved findings, and false positives.
- Cosmetic metric reductions that leave the underlying issue in place.
- Total tokens, elapsed time, and retry count, recorded separately from correctness.
- Missing tools, malformed findings, timeouts, and permission-boundary behavior.

This research verified public docs, source configuration, and package metadata. Context7 returned unrelated habit-tracking libraries, so the tool's own repository and package registry supplied the product evidence. No PolicyNIM runtime or CI behavior was changed.
