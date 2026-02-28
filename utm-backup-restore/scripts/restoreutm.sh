#!/bin/zsh
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  restoreutm.sh <utm-zip-file> [--restore-dir PATH] [--dry-run]

Restores a UTM zip (created with ditto) into the default UTM Documents folder
or a custom restore directory.

Args:
  <utm-zip-file>         Path to the .zip to restore

Options:
  --restore-dir PATH     Restore target directory
                         (default: ~/Library/Containers/com.utmapp.UTM/Data/Documents)
  --dry-run              Print actions without writing
  -h, --help             Show this help

Notes:
  - Prefer restoring while UTM is quit to avoid file contention.
EOF
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

if [[ "$(uname)" != "Darwin" ]]; then
  echo "Error: This script requires macOS (Darwin)." >&2
  exit 1
fi

if [ "$#" -eq 0 ]; then
  usage >&2
  exit 2
fi

zip_path=""
restore_dir="$HOME/Library/Containers/com.utmapp.UTM/Data/Documents"
dry_run=false

while [ "$#" -gt 0 ]; do
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    --restore-dir)
      restore_dir="${2:-}"
      if [ -z "$restore_dir" ] || [[ "$restore_dir" == --* ]]; then
        echo "--restore-dir requires a PATH" >&2
        exit 2
      fi
      shift 2
      ;;
    --dry-run)
      dry_run=true
      shift
      ;;
    --*)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
    *)
      if [ -n "$zip_path" ]; then
        echo "Unexpected extra arg: $1" >&2
        usage >&2
        exit 2
      fi
      zip_path="$1"
      shift
      ;;
  esac
done

if [ -z "$zip_path" ]; then
  echo "Missing <utm-zip-file>" >&2
  usage >&2
  exit 2
fi

if [ ! -f "$zip_path" ]; then
  echo "File not found: $zip_path" >&2
  exit 2
fi

if [ "$dry_run" = true ]; then
  echo "Would restore: $zip_path"
  echo "To directory:  $restore_dir"
  exit 0
fi

mkdir -p "$restore_dir"

# Use ditto for extraction to preserve macOS bundle metadata.
ditto -x -k "$zip_path" "$restore_dir"

echo "Restored to $restore_dir"
