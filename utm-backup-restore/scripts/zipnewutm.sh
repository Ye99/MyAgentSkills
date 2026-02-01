#!/bin/zsh
set -euo pipefail

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Error: This script requires macOS (Darwin)." >&2
  exit 1
fi

usage() {
  cat <<'EOF'
Usage:
  zipnewutm.sh [--dir PATH] [--force] [--dry-run] [--no-color] [--no-verbose]

Zips UTM .utm bundles using ditto to preserve macOS bundle metadata.

Defaults:
  - If --dir is omitted, uses the current working directory.
  - Skips any .utm where a same-name .zip already exists (unless --force).

Options:
  --dir PATH     Directory containing .utm bundles to zip
  --force        Overwrite existing .zip files
  --dry-run      Print actions without writing
  --no-color     Disable ANSI colors
  --no-verbose   Do not pass -V to ditto
  -h, --help     Show this help
EOF
}

dir=""
force=false
dry_run=false
no_color=false
verbose=true

while [ "$#" -gt 0 ]; do
  case "$1" in
    --dir)
      dir="${2:-}"
      if [ -z "$dir" ]; then
        echo "--dir requires a PATH" >&2
        exit 2
      fi
      shift 2
      ;;
    --force)
      force=true
      shift
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --no-color)
      no_color=true
      shift
      ;;
    --no-verbose)
      verbose=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "$dir" ]; then
  if [ ! -d "$dir" ]; then
    echo "Directory not found: $dir" >&2
    exit 2
  fi
  cd "$dir"
fi

if [ -t 1 ] && [ "$no_color" = false ]; then
  COLOR_RESET=$'\033[0m'
  COLOR_INDEX=$'\033[1;34m'
  COLOR_ACTION=$'\033[1;33m'
  COLOR_NAME=$'\033[1;32m'
  COLOR_ARROW=$'\033[1;90m'
  COLOR_SKIP=$'\033[1;36m'
else
  COLOR_RESET=""
  COLOR_INDEX=""
  COLOR_ACTION=""
  COLOR_NAME=""
  COLOR_ARROW=""
  COLOR_SKIP=""
fi

utm_list=( *.utm(N) )
total_count="${#utm_list[@]}"
if [ "$total_count" -eq 0 ]; then
  echo "No .utm bundles found in: $(pwd)"
  exit 0
fi

current_index=0
for utm in "${utm_list[@]}"; do
  current_index=$((current_index + 1))
  zip_name="${utm%.utm}.zip"

  if [ -e "$zip_name" ] && [ "$force" = false ]; then
    printf "%b[%d/%d]%b %bSkipping%b %b%s%b (%bzip exists%b)\n" \
      "$COLOR_INDEX" "$current_index" "$total_count" "$COLOR_RESET" \
      "$COLOR_SKIP" "$COLOR_RESET" \
      "$COLOR_NAME" "$utm" "$COLOR_RESET" \
      "$COLOR_ARROW" "$COLOR_RESET"
    continue
  fi

  printf "%b[%d/%d]%b %bZipping%b %b%s%b %b->%b %b%s%b\n" \
    "$COLOR_INDEX" "$current_index" "$total_count" "$COLOR_RESET" \
    "$COLOR_ACTION" "$COLOR_RESET" \
    "$COLOR_NAME" "$utm" "$COLOR_RESET" \
    "$COLOR_ARROW" "$COLOR_RESET" \
    "$COLOR_NAME" "$zip_name" "$COLOR_RESET"

  if [ "$dry_run" = true ]; then
    continue
  fi

  # Use ditto for zip creation to preserve macOS bundle metadata.
  # -V is the only native verbose output available.
  if [ "$verbose" = true ]; then
    ditto -c -k --sequesterRsrc --keepParent -V "$utm" "$zip_name"
  else
    ditto -c -k --sequesterRsrc --keepParent "$utm" "$zip_name"
  fi

  printf "%b[%d/%d]%b %bDone%b %b%s%b\n" \
    "$COLOR_INDEX" "$current_index" "$total_count" "$COLOR_RESET" \
    "$COLOR_ACTION" "$COLOR_RESET" \
    "$COLOR_NAME" "$zip_name" "$COLOR_RESET"
done

echo "done"
