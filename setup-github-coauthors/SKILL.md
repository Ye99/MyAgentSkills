---
name: setup-github-coauthors
description: Use when a user wants OpenCode, Claude, and Codex to appear as GitHub co-authors in commit history for a specific repository using valid numeric-id noreply emails.
---

# Set Up GitHub Co-Authors

Configure a repository-local commit template so future commits include valid `Co-authored-by` trailers for OpenCode, Claude, and Codex.

Core rule: never guess noreply emails. Always resolve the numeric GitHub account ID first, then build `<id>+<username>@users.noreply.github.com`.

## When to Use

- The user wants these co-authors to appear in personal repositories.
- The user wants setup for one repository (local git config), not global defaults.
- The user wants valid GitHub attribution that links to real accounts.

Do not use this skill when the user asks for global `~/.gitconfig` setup across all repositories.

## Prerequisites

- Each account exists on GitHub.
- In each account: `Settings -> Emails` has both enabled:
  - `Keep my email addresses private`
  - `Block command line pushes that expose my email`
- You know the exact account usernames (defaults: `opencode`, `claude`, `openai`).
- For repository URLs like `openai/codex`, use the owner account (`openai`) to build noreply email.

## Recommended Workflow

1. Run the helper script in this skill to generate `.gitmessage` and set repo-local `commit.template`.
2. Verify `git config --show-origin --get commit.template` points to `<repo>/.gitmessage`.
3. Confirm `.gitmessage` has three uncommented `Co-authored-by` lines.
4. Remind the user to commit with `git commit` (not `git commit -m`) so the template is loaded.

## Helper Script

Path: `scripts/setup-repo-coauthors.sh`

```bash
bash scripts/setup-repo-coauthors.sh <repo-path> [opencode-username] [claude-username] [codex-owner-username]
```

Example:

```bash
bash scripts/setup-repo-coauthors.sh ~/p/some-repo
```

Example with custom usernames:

```bash
bash scripts/setup-repo-coauthors.sh ~/p/some-repo opencode-bot claude openai
```

## Verification Checklist

- `git -C <repo> config --show-origin --get commit.template` returns `<repo>/.gitmessage` from `.git/config`.
- `.gitmessage` contains:
  - `Co-authored-by: OpenCode <id+username@users.noreply.github.com>`
  - `Co-authored-by: Claude <id+username@users.noreply.github.com>`
  - `Co-authored-by: Codex <id+username@users.noreply.github.com>`
- No guessed IDs and no placeholder emails.

## Common Mistakes

| Mistake | Fix |
|---|---|
| Guessing noreply email format | Resolve ID from `https://api.github.com/users/<username>` first |
| Using wrong username | Use exact login from the created account |
| Using repo path as username (for example `openai/codex`) | Use the repo owner login (`openai`) |
| Commenting out co-author lines with `#` | Keep `Co-authored-by` trailers uncommented |
| Setting global template accidentally | Use `git config --local commit.template ...` |
| Using `git commit -m` | Use `git commit` so template is inserted |
