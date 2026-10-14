#!/usr/bin/env bash
# Save every piece of a git repo's work that is not on its remote, as plain files,
# so the repo (or just its .git) can then be deleted and re-cloned later.
#
# Usage: bash rescue_git_work.sh HOME REPO
# Writes HOME/RescuedGitWork/<repo path, / as __, leading dots dropped>/ with README.txt
# (remote, base commit on the remote, restore steps), unpushed-commits/*.patch, stash-*.patch,
# uncommitted.patch and files/ (copies of modified and untracked files).
# Untracked build output (node_modules, build, dist, .idea, ...) is skipped even if not ignored.
# Always prints two lines first:
#   owner: person|third-party|unknown  (does any commit author match HOME/.gitconfig user.email?)
#   rescued: <dir>  or  nothing to rescue: <repo>
set -euo pipefail
home=$(cd "$1" && pwd) repo=$(cd "$2" && pwd)
REGENERABLE='(^|/)(node_modules|build|dist|out|target|\.gradle|\.idea|\.vs|\.next|\.expo|__pycache__|\.venv|venv|\.pytest_cache|\.ipynb_checkpoints|\.cxx|Pods)(/|$)|\.(pyc|o|so|class|dex|apk|aab|map|tsbuildinfo)$'

me=$(git config -f "$home/.gitconfig" user.email 2>/dev/null || true)
if [ -z "$me" ]; then
  echo "owner: unknown (no user.email in $home/.gitconfig)"
elif git -C "$repo" log --all --format=%ae 2>/dev/null | grep -qixF "$me"; then
  echo "owner: person"
else
  echo "owner: third-party"
fi

remote=$(git -C "$repo" remote get-url origin 2>/dev/null || echo "")
shown_remote=$(printf '%s' "$remote" | sed -E 's#(://)[^/@]+@#\1#')   # never copy tokens into the archive
if [ -z "$remote" ]; then
  range=(--all)                                                   # nothing is on a remote
  base=""
else
  range=(--branches --tags --not --remotes)
  upstream=$(git -C "$repo" rev-parse --abbrev-ref '@{u}' 2>/dev/null \
    || git -C "$repo" rev-parse --abbrev-ref origin/HEAD 2>/dev/null || true)
  base=$( [ -n "$upstream" ] && git -C "$repo" merge-base HEAD "$upstream" 2>/dev/null || true)
fi
unpushed=$(git -C "$repo" rev-list "${range[@]}" 2>/dev/null | wc -l)
changed=$(git -C "$repo" status --porcelain --untracked-files=all | awk '{print $NF}' | grep -cvE "$REGENERABLE" || true)
stashes=$(git -C "$repo" stash list | wc -l)

if [ "$unpushed" -eq 0 ] && [ "$changed" -eq 0 ] && [ "$stashes" -eq 0 ]; then
  echo "nothing to rescue: $repo"
  exit 0
fi

name=$(realpath --relative-to="$home" "$repo" | sed -E 's#/#__#g; s#(^|__)\.+#\1#g')
out="$home/RescuedGitWork/$name"
mkdir -p "$out"
{
  echo "repo:     ${shown_remote:-(no remote; full history in all-history.bundle)}"
  echo "base:     ${base:-(none)}   # last commit the remote already has"
  echo "unpushed commits: $unpushed   changed files: $changed   stashes: $stashes"
  echo "restore:  git clone <repo> && cd <repo> && git checkout -b restored <base>"
  echo "          git am <this dir>/unpushed-commits/*.patch     # if present"
  echo "          git apply <this dir>/uncommitted.patch          # if present"
  echo "          cp -r <this dir>/files/. .                      # untracked and edited files"
} > "$out/README.txt"

if [ "$unpushed" -gt 0 ]; then
  [ -z "$remote" ] && git -C "$repo" bundle create "$out/all-history.bundle" --all >/dev/null 2>&1
  git -C "$repo" format-patch -q -o "$out/unpushed-commits" "${range[@]}"
fi

for i in $(seq 0 $((stashes - 1))); do
  git -C "$repo" stash show -p --include-untracked "stash@{$i}" > "$out/stash-$i.patch" 2>/dev/null \
    || git -C "$repo" stash show -p "stash@{$i}" > "$out/stash-$i.patch"
done

if [ "$changed" -gt 0 ]; then
  git -C "$repo" diff --binary HEAD > "$out/uncommitted.patch" 2>/dev/null || true
  [ -s "$out/uncommitted.patch" ] || rm -f "$out/uncommitted.patch"
  { git -C "$repo" diff --name-only --diff-filter=AM HEAD 2>/dev/null || true
    git -C "$repo" ls-files --others --exclude-standard; } |
  grep -vE "$REGENERABLE" |
  while IFS= read -r f; do
    [ -f "$repo/$f" ] || continue
    mkdir -p "$out/files/$(dirname "$f")"
    cp -p "$repo/$f" "$out/files/$f"
  done
fi
echo "rescued: $repo -> $out"
find "$out" -type f | sed "s#^$out/#  #"
