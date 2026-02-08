## Engineering principle

Things should be as simple as possible, and no simpler.

## Authoring README Policy (Global)

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

## Git Linear History Policy (Global)

When pushing branches, prefer a linear history.

- Before any `git push`, run `git pull --rebase` so remote changes are applied on top of the local branch.
- Do not use merge-based pulls (avoid merge commits). Prefer rebase to keep history linear.
