#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  setup-repo-coauthors.sh <repo-path> [opencode-username] [claude-username] [codex-username]

Defaults:
  opencode, claude, codex-cli

Examples:
  setup-repo-coauthors.sh ~/p/some-repo
  setup-repo-coauthors.sh ~/p/some-repo opencode claude codex-cli
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ $# -lt 1 || $# -gt 4 ]]; then
  usage >&2
  exit 1
fi

repo_path="$1"
opencode_user="${2:-opencode}"
claude_user="${3:-claude}"
codex_user="${4:-codex-cli}"

if ! git -C "$repo_path" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "error: '$repo_path' is not a git repository" >&2
  exit 1
fi

get_id() {
  local username="$1"
  local response
  local id

  if ! response="$(curl -fsSL "https://api.github.com/users/${username}")"; then
    echo "error: could not resolve GitHub user '${username}'" >&2
    return 1
  fi

  id="$(python3 -c 'import json,sys; data=json.load(sys.stdin); print(data.get("id", ""))' <<<"$response")"

  if [[ -z "$id" ]]; then
    echo "error: could not parse numeric ID for '${username}'" >&2
    return 1
  fi

  printf '%s' "$id"
}

opencode_id="$(get_id "$opencode_user")"
claude_id="$(get_id "$claude_user")"
codex_id="$(get_id "$codex_user")"

template_path="${repo_path%/}/.gitmessage"

cat > "$template_path" <<EOF
# Summary:
#
#
Co-authored-by: OpenCode <${opencode_id}+${opencode_user}@users.noreply.github.com>
Co-authored-by: Claude <${claude_id}+${claude_user}@users.noreply.github.com>
Co-authored-by: Codex <${codex_id}+${codex_user}@users.noreply.github.com>
EOF

git -C "$repo_path" config --local commit.template "$template_path"

echo "Wrote: $template_path"
echo "Configured: $(git -C "$repo_path" config --show-origin --get commit.template)"
echo "Reminder: use 'git commit' (without -m) so the template is loaded."
