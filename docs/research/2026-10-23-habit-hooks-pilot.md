# Habit Hooks advisory trial

Date: October 23, 2026. This trial ran Habit Hooks detectors and coaching on PolicyNIM source code. It used no agent/model repair loop and made no provider requests.

## Reproduce

```bash
uvx --from 'habit-hooks[python]==1.5.0' --with 'ruff==0.17.1' --with 'deptry==0.25.1' habit-hooks --all
```

The checked-in configuration enables Python and generic guides, scopes Python source/tests/scripts, and makes findings advisory. It disables the jscpd sensor because this trial doesn't install Node duplication tooling. No runner scripts, automatic fixes, snoozes, or CI enforcement are enabled. Findings remain tool data within the user's authorized task.

## Observations

The unchanged baseline was Git revision `0956cca74a7d83091839a03b0d4d1838d54b825f`. Both runs used the same detector versions and project configuration. The current run measured the pre-review implementation snapshot; its Python-file digest appears below. Counts describe detector occurrences across the whole repository, including existing backlog.

| Smell | Baseline occurrences | Implementation snapshot occurrences |
| --- | --- | --- |
| high-complexity | 58 | 61 |
| oversized-function | 42 | 43 |
| swallowed-exception | 42 | 42 |
| too-many-parameters | 58 | 58 |
| unused-dependency | 10 | 11 |
| oversized-file | 119 | 122 |

The sensor and mapper stages each completed with exit code 0 under the advisory configuration. That exit code records a completed advisory run; the table shows the findings it retained.

Coaching about mixed responsibilities prompted separating setup's build, current-generation check, and result-verification methods. Other findings need judgment: the optional Psycopg driver is imported lazily and exercised by real pgvector tests, while the dependency detector classifies it as unused. Setup persistence guards, the HTTP app's nested handlers, and an end-to-end lifecycle scenario also cross structural thresholds. Their counts alone don't justify splitting cohesive behavior or changing verified error handling.

No prior backlog was snoozed or marked resolved. Existing Ruff, Pyright, runtime tests, and Qodo review remain the delivery gates. Lower token use, behavior-preserving agent repairs, false-positive rates, and comparative model performance weren't measured. A future repair experiment needs matched tasks, independent behavior checks, and separate cost measurements.

## Snapshot binding

Python files under `src/`, `tests/`, and `scripts/` are sorted by path and hashed as path bytes, NUL, file bytes, NUL. The implementation snapshot digest is `7e7d31aa27e8018e6c625ea18ebb668d3d963a67f507ddf4bb6e310ce6d5b8d7`. Subsequent source changes need a new measurement before these counts describe them.

See [the product research](2026-10-22-habit-hooks-source-notes.md) and [the tool's architecture](https://github.com/habit-hooks/habit-hooks/blob/5603331f8c31b1537761e6639f49b876acf0a4e4/docs/architecture.md).
