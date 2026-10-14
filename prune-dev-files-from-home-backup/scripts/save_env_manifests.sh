#!/usr/bin/env bash
# Before deleting Python environments, record what was installed in them, so each one
# can be rebuilt. Reads metadata files directly: no conda or pip needed.
#
# Usage: bash save_env_manifests.sh ROOT OUT_DIR
# Writes one OUT_DIR/<env path with / as __>.txt per environment found under ROOT:
#   conda envs  -> "name==version" from conda-meta/*.json
#   venvs/other -> "name==version" from site-packages/*.dist-info folder names
set -euo pipefail
root=$(cd "$1" && pwd) out=$2
mkdir -p "$out"

manifest() {   # $1 = environment dir, $2 = kind
  local env=$1 kind=$2 name
  name=$(realpath --relative-to="$root" "$env" | sed 's#/#__#g')
  {
    echo "# $kind environment: $(realpath --relative-to="$root" "$env")"
    if [ "$kind" = conda ]; then
      for j in "$env"/conda-meta/*.json; do
        [ -e "$j" ] || continue
        b=$(basename "$j" .json)                       # numpy-1.26.4-py312_0
        echo "${b%-*}" | sed -E 's/-([^-]+)$/==\1/'      # numpy==1.26.4
      done
    fi
    find "$env" -maxdepth 5 -type d -name '*.dist-info' -path '*site-packages*' -printf '%f\n' |
      sed -E 's/\.dist-info$//; s/-([^-]+)$/==\1/' | sort -u
  } > "$out/$name.txt"
  echo "saved $out/$name.txt ($(($(wc -l < "$out/$name.txt") - 1)) packages)"
}

find "$root" -type d -name conda-meta -not -path '*/pkgs/*' -prune -print 2>/dev/null |
  while IFS= read -r m; do manifest "$(dirname "$m")" conda; done
find "$root" -type f -name pyvenv.cfg -not -path '*/conda-meta/*' 2>/dev/null |
  while IFS= read -r c; do manifest "$(dirname "$c")" venv; done
