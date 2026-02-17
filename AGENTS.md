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

## Search Protocol for .md Files

When searching or reading markdown files:
- Do not read whole file if they are large.
- Read smartly using tools (grep/search first).

## Markdown/Obsidian Linking Policy

When creating internal links to headers (e.g., `[[#Header Name]]`):
- **Keep headers clean**: Do not include links or markdown formatting inside the header itself (e.g., use `### Header` instead of `### [Header](url)`).
- **Place links in content**: Put external links in the body text immediately following the header.
- **Reason**: Links inside headers break the anchor generation for internal linking in Obsidian and many markdown parsers.

