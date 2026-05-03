---
name: claude-cli-review-loop
description: Use when code changes need independent local Claude CLI review, especially after complex fixes, external feedback, or requests to repeat review/fix cycles until no medium-or-higher issues remain.
---

# Claude CLI Review Loop

Use the installed `claude` CLI as an external reviewer. Treat its output as review feedback to reason about and verify, not as ground truth.

## When to use

- The user asks for Claude CLI review, another-agent review, or repeated review/fix loops.
- A non-trivial code or skill change needs independent scrutiny before completion.
- Prior feedback is subtle, disputed, or likely to create regressions if applied blindly.

Do not use this for tiny edits where local tests and direct review are enough, or when `claude` is unavailable.

## Loop

1. Confirm the CLI:

```bash
command -v claude
claude --help
```

2. Invoke Claude with structurally read-only tools. Prefer `-p`, `--permission-mode plan`, `--add-dir`, and an explicit read-only tool list:

```bash
claude -p --permission-mode plan --tools "Read,Grep,Glob" --add-dir /path/to/project --output-format text \
  "Review the current implementation for medium-or-higher correctness regressions, edge cases, data-loss risks, and materially false tests/docs. Do not edit files. Inspect these files: [paths]. For each finding include severity, exact area, why it is real, minimal repro, and recommended fix. If no medium-or-higher issues remain, say exactly: NO MEDIUM OR HIGHER ISSUES FOUND."
```

Do not give the reviewer `Edit`, `Write`, `MultiEdit`, `NotebookEdit`, or unrestricted `Bash`. If the reviewer needs command output, run the command yourself and include the output in the next prompt.

If `claude --help` on the host does not list `--tools`, use the documented allowlist flag instead, such as `--allowedTools "Read,Grep,Glob"` or `--allowed-tools "Read,Grep,Glob"`.

3. Fact-check every finding before editing:

- Verify every cited file path, symbol, function, and test name exists. If not, classify it as `false: hallucinated reference` and do not debate the substance.
- Restate the claimed failure mode in concrete terms.
- Trace it against the actual code and tests.
- Reproduce it with a local probe or failing test when possible.
- Classify it as true medium+, true low, false, duplicate, or intentionally out of scope.
- Reject false or low-severity findings with technical reasoning.
- Fix only verified true issues that meet the requested threshold.

4. For each true medium+ issue:

- Write a failing regression test first, unless it is documentation-only.
- Apply the smallest fix that addresses the verified root cause.
- Run the targeted test, then the relevant full test/build/syntax checks.

5. Carry reasoning into the next Claude pass. Include an append-only review ledger:

```text
Scope:
- Files reviewed: [stable path list]
- Current change summary: [brief diff/test summary for this round]

Previous Claude findings:
- Round N fixed as true: [finding -> evidence/test/fix]
- Round N rejected as false: [finding -> code/test evidence]
- Round N classified low/out-of-scope: [finding -> reason]
- Prior-round rejected/low/out-of-scope findings retained: [append-only list]

Please fact-check both the current code and my classifications above. If you disagree with a rejection, give a concrete repro or trace. Report only remaining medium-or-higher issues, or say exactly: NO MEDIUM OR HIGHER ISSUES FOUND.
```

This creates a debate loop: Claude can challenge your pushbacks, and you can verify that challenge with code and tests. Do not drop rejected findings silently between rounds.

6. Repeat review/fix cycles until Claude returns `NO MEDIUM OR HIGHER ISSUES FOUND`, or until every remaining finding is verified false, low severity, duplicate, or out of scope after being carried through at least one follow-up Claude pass.

## Feedback Rules

Follow `superpowers:receiving-code-review`:

- Do not blindly treat Claude's feedback as true.
- Do not performatively agree.
- Do not implement unverified suggestions.
- Push back with evidence when a claim is wrong for this codebase.
- Keep a concise true/false/out-of-scope list for the user.

## Operational Notes

If Claude stalls, wait a reasonable interval, then rerun with a narrower file list and stricter output request. Do not edit files while a Claude review process is still running.

Final reports should include review pass count, true issues fixed, false or out-of-scope findings, verification commands/results, and changed files.
