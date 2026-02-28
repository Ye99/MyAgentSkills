---
name: review-tests
description: Use when reviewing a test suite for redundancy, maintainability, or removal candidates while keeping failures easy to diagnose.
---

# Review Tests

## Overview

Tests are diagnostic tools first, coverage tools second.

In this context, a good test failure should tell you what behavior broke from the test name plus assertion output, without deep code archaeology.

## When to Use

Use this skill when asked to:
- review test quality
- find unnecessary tests
- decide whether to merge/split tests
- improve test maintainability without weakening behavior protection

Do not use this skill when writing net-new feature behavior from scratch; use test-driven development first.

## Core Rules

1. Keep one behavioral contract per test whenever practical.
2. Prefer atomic tests over combined assertions when diagnosis speed would drop.
3. Remove tests only if equivalent behavioral coverage remains.
4. Keep tests for high-risk contracts (auth, security, persistence, config compatibility) even if they look repetitive.
5. Favor behavior assertions over implementation-order assertions.
6. Test names must explain failure impact, not implementation mechanics.

## Merge vs Split Decision

Split tests when:
- test title contains multiple behaviors joined by "and"
- one failure can have multiple plausible causes
- one fixture setup drives several unrelated assertions

Merge or table-drive tests only when:
- each case has a distinct, readable case name
- failure output identifies exactly which case failed
- all cases validate the same contract shape

Rule of thumb: if likely root cause is not inferable within 30 seconds from failure output, split the test.

## Remove / Keep Rubric

Mark as **Remove (low risk)** only if all are true:
- another test already protects the same behavior branch
- removed test does not add clearer diagnostics
- removed test is not the only cross-boundary or regression guard

Mark as **Refactor** when:
- coverage is useful but setup is repetitive
- assertions are right but names are unclear
- table-driven structure would preserve per-case diagnosability

Mark as **Keep** when:
- test protects subtle regressions (state, refresh, concurrency, merge semantics)
- test validates compatibility contracts (legacy/modern config, user overrides)
- test captures user-visible failure behavior

## Practical Review Workflow

1. Run tests and inspect failing names/messages first.
2. Group tests by protected contract, not by file.
3. Identify duplicates, then check if any duplicate is more diagnosable.
4. Prefer deleting the less-diagnostic duplicate.
5. Re-run full suite and coverage after changes.
6. Report decisions as: Remove now / Refactor / Keep, with one-line rationale each.

## Output Format for Reviews

- `Remove now`: path + test name + why low risk
- `Refactor`: path + what to change + why diagnostics stay strong
- `Keep`: path + protected risk/contract
- `Confidence`: 0-100 with what evidence would change the decision

## Common Mistakes

- Removing a test because it looks similar without mapping exact protected branch.
- Merging tests and losing clarity of failure cause.
- Chasing coverage percentage while reducing debugging speed.
- Deleting "annoying" edge-case tests that actually prevent regressions.
