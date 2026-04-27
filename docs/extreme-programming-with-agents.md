# Extreme Programming with Agents: How I Use Codex, TDD, BDD, Skills, and Review Layers

Agentic coding gets dangerous when code generation becomes the first step.

That is the trap inside a lot of AI workflow advice. Open Codex, ask for code, get something plausible, and hope review catches the rest. It feels fast, but it pushes feedback too far to the right. By the time you discover the design is wrong or the tests are weak, you already have a diff and a mess to unwind.

Extreme Programming taught a better habit a long time ago. Create feedback first. Then write code.

That is why XP matters more in the age of agents, not less. Agents collapse the cost of typing. They do not collapse the cost of bad decisions.

## Why agents are rediscovering XP

XP was built for unstable environments. Requirements move. Understanding changes as you work. The safest response is short feedback loops, tests first, simple design, and constant review.

Modern coding agents fit that world almost too well. Codex is a strong pair programmer. It is not the whole system. It should not be deciding the plan, writing unchecked production code, judging its own output, and declaring the work done. When teams use it that way, they are not practicing leverage. They are skipping feedback.

What XP gives me is an operating system for that speed. It tells me where the agent belongs, what order work should happen in, and how the system stays honest.

## The two-layer system I use on PolicyNIM

PolicyNIM is the project. The method is the point.

I use a two-layer workflow. The first layer handles sequencing. What are we building, in what order, and what behavior are we trying to prove? The second layer handles quality. Is the slice correct, tested, idiomatic, and safe to keep?

Codex lives inside that system as the pair programmer in the inner loop. It is there to help me move through small slices quickly. It is not the planner, the reviewer of record, or the approver.

The whole loop from spec to merge looks like this:

1. Start from a story or spec, then write the behavior in Given/When/Then form.
2. Ask Codex for failing tests only, not implementation.
3. Implement the smallest change that gets the test green.
4. Refactor while the tests stay green.
5. Run outer-loop gates such as `pre-commit`, `ruff`, `pytest`, and an independent review pass.
6. Put a human in the final review before merge.

That separation matters. The inner loop keeps momentum high. The outer loop keeps it honest.

## Inner loop: TDD and BDD with Codex

In the inner loop, I want Codex thinking like a disciplined pair, not a fast typist with repo access. So I start from behavior, not files.

On PolicyNIM, one useful example is fail-closed preflight behavior. If the system cannot ground a task strongly enough, I do not want a polished bluff. I want the result to return `insufficient_context=true`. When grounding is strong enough, I want the output to surface concrete `review_flags` and `tests_required` rather than vague advice.

That becomes a BDD-style slice before it becomes code: given a vague task and weak retained evidence, preflight should fail closed instead of inventing guidance; given a grounded task with valid evidence, preflight should preserve the review flags and test expectations that fall out of that evidence.

From there I have Codex generate or extend the tests only. No implementation yet. This is the part many agent workflows skip, and it is exactly where the discipline matters.

Once the tests fail for the right reason, Codex helps implement the smallest change that makes them pass. After that, we refactor. Rename things. Flatten logic. Remove speculative structure. Keep the design plain until the next behavior forces it to grow.

This is still classic XP. The only difference is that the pair programmer can move much faster than a human pair. That makes the order of operations more important, not less.

## Outer loop: independent review and quality governance

This is where a lot of agent workflows fall apart. They let the same system that generated the code act as planner, implementer, reviewer, and closer. That is not review. That is one model talking to itself.

On PolicyNIM, I want the boring safeguards on purpose. I use `pre-commit` so the local workflow catches obvious issues early. I run `ruff` to keep the Python readable and consistent. I run `pytest` because the behavior has to survive contact with the test suite, not just the prompt. Then I add an independent review layer. Qodo fits here because I want another system looking at the diff with a different job.

That outer loop is not bureaucracy. It is risk management. It is how the system stays honest after the inner loop goes green.

And yes, human review still matters. Agents can generate, lint, test, and flag. They do not own the codebase consequences. People do.

## Packaging the discipline as a reusable skill

So I turn the discipline into a reusable skill. I like the name `xp_tdd_story_runner` because it says exactly what it should do. It takes a spec or story, breaks it into behavior slices, generates failing tests first, implements minimally, and leaves clear repo guidance so the next session does not drift.

The point is consistency. When I am tired or in a rush, I do not want the workflow to depend on memory.

## Prompt: have your coding agent design the skill

This is the prompt I would give a coding agent first.

```text
You are my engineering workflow designer.

Goal:
Design a reusable repository skill called `xp_tdd_story_runner` that enforces an Extreme Programming workflow for feature work.

Context:
- I use Codex as my coding agent.
- I want the inner loop to follow: behavior (BDD) -> failing tests -> minimal implementation -> refactor with tests passing.
- I want the outer loop to include linting, test execution, independent review, and human review before merge.
- This skill should improve consistency across sessions, not generate a giant scaffold.

Deliverables:
1. Skill design
   - Define inputs, outputs, phases, and decision rules.
   - Show how the skill turns a spec or PRD into user stories and Given/When/Then scenarios.
   - Show how it enforces tests-first before implementation.
2. Repo documentation updates
   - Propose concrete updates for repo instructions such as AGENTS.md, WORKFLOW.md, or equivalent files.
   - Make the guidance explicit about when to use the skill and what "done" means.
3. Usage prompts
   - Provide short reusable prompts for:
     - implementing from a PRD or spec
     - implementing from an issue or ticket
     - refactoring existing code with tests still in control

Constraints:
- Do not design enforcement automation that bypasses review or merge.
- Do not replace independent review or human review.
- Prefer clear workflow rules, small examples, and repo-documentation updates over large placeholders.
- Ask clarifying questions only if a repo-specific detail is required to make the skill accurate.
```

## How to start small

Pick one active project. Choose one story that matters. Write the behavior in Given/When/Then form before Codex touches implementation. Force the first pass to be tests only. Add the ordinary gates that keep the work clean: `pre-commit`, `ruff`, `pytest`, and one independent reviewer. Once the workflow works twice in a row, package it as a skill so you stop renegotiating the same standards every session.

That is the real win. With the right XP discipline around them, agents let you run more feedback loops before a bad decision hardens into software.
