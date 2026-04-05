## Engineering Principle

Keep things as simple as possible, and no simpler.

## Authoring README Policy

When modifying a repository, update `README.md` only when the change materially affects users or contributors:
- setup, install, or run steps
- public interfaces, commands, or config
- architecture or behavior constraints
- important operational caveats

Do not update `README.md` for trivial changes:
- formatting-only edits
- minor refactors or renames
- typo-only fixes
- internal test-only changes

Keep README edits minimal, precise, and user-relevant.

## Git Linear History Policy

When pushing branches, prefer a linear history.

- Stage and commit local changes with a descriptive message before running `git pull --rebase`.
- Before any `git push`, run `git pull --rebase` so remote changes are replayed on top of the local branch.
- Do not use merge-based pulls. Prefer rebase to avoid merge commits.

## Git Co-Author Policy

When a repository is configured with local co-author settings, preserve them on agent-authored commits.

- Before committing, check `git config --local --get commit.template`.
- If a local template exists and contains `Co-authored-by:` trailers, keep those trailers in the final commit message.
- If committing non-interactively (for example with `git commit -m`), append the same `Co-authored-by:` lines explicitly as one contiguous trailer block at the end of the message (no blank lines between trailer lines).
- After committing, verify trailer parsing with `git show -s --format=%B HEAD | git interpret-trailers --parse` and confirm all expected `Co-authored-by:` entries are present.
- Do not invent or replace co-author emails; use the values defined by the repository template.

## Search Protocol for .md Files

When searching or reading Markdown files:
- Do not read whole files if they are large.
- Read them selectively using search tools first.

## Markdown Note Append Policy

When appending text to a note, insert it **before the first embedded-image block** (for example, a `data:image` reference). Embedded images stay at the bottom of note files; preserving that boundary keeps manual editing simple.

## Retry-once Recovery

If you see an error like `"text part msg_0f33dbbb86dbf3db0169b8646639dc819c8a533b412f63ed97 not found"`, the `0f33dbbb86dbf3db0169b8646639dc819c8a533b412f63ed97` portion will change each time. It may come from the LLM or from the API layer dropping a message part. Retry the failed step; it will usually recover.

If you hit an `SSE read timed out` error, retry the failed step once before doing anything more invasive; it usually recovers on the next attempt.

## Python Tests

Prefer `pytest` over `unittest` for Python tests. It provides fixtures and cleaner temporary-directory handling with less boilerplate.
