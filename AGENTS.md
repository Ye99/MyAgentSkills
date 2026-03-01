## Engineering principle

Things should be as simple as possible, and no simpler.

## Authoring README Policy

When modifying a repository, if `README.md` exists, update it only when the change materially affects users or contributors:
- setup/install/run steps
- public interfaces/commands/config
- architecture/behavior constraints
- important operational caveats

Do NOT update README for trivial changes:
- formatting-only edits
- minor refactors/renames
- typo-only fixes
- internal test-only changes

Apply the engineering principle above, keep edits minimal, precise, and user-relevant.

## Git Linear History Policy

When pushing branches, prefer a linear history.

- Stage and commit local changes with a descriptive message before running `git pull --rebase`.
- Before any `git push`, run `git pull --rebase` so remote changes are applied on top of the local branch.
- Do not use merge-based pulls (avoid merge commits). Prefer rebase to keep history linear.

## Git Co-Author Policy

When a repository is configured with local co-author settings, preserve them on agent-made commits.

- Before committing, check `git config --local --get commit.template`.
- If a local template exists and contains `Co-authored-by:` trailers, keep those trailers in the final commit message.
- If committing non-interactively (for example with `git commit -m`), append the same `Co-authored-by:` lines explicitly as one contiguous trailer block at the end of the message (no blank lines between trailer lines).
- After committing, verify trailer parsing with `git show -s --format=%B HEAD | git interpret-trailers --parse` and confirm all expected `Co-authored-by:` entries are present.
- Do not invent or replace co-author emails; use the values defined by the repository template.

## Search Protocol for .md Files

When searching or reading markdown files:
- Do not read whole files if they are large.
- Read smartly using tools (grep/search first).

## Markdown Note Append Policy

When appending text to a note, insert it **before the first embedded-image block** (for example, a `data:image` reference). Embedded images are kept at the bottom of note files; preserving this boundary keeps manual editing simple.
