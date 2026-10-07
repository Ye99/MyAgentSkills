#!/usr/bin/env bash
# Check every file listed in a backup's nvpartitionmap.txt against the SHA-256
# the Jetson recorded while writing it, and that the APP archive is readable.
#
# usage: bash verify-backup.sh <backup_dir>
set -euo pipefail

dir=$(realpath "${1:?usage: verify-backup.sh <backup_dir>}")
map="$dir/nvpartitionmap.txt"
[ -f "$map" ] || { echo "ERROR: $map missing" >&2; exit 1; }

fail=0 n=0
# Lines: board_spec,<spec>  then  file,partition,start,size,type,sha256
while IFS=, read -r file part start size type sum; do
	[ "$file" = board_spec ] && { echo "board_spec: $part"; continue; }
	n=$((n + 1))
	if [ ! -f "$dir/$file" ]; then
		echo "MISSING  $file"; fail=1; continue
	fi
	if [ "$(sha256sum "$dir/$file" | awk '{print $1}')" = "$sum" ]; then
		echo "OK       $file ($part)"
	else
		echo "BAD SUM  $file"; fail=1
	fi
done < "$map"

for t in "$dir"/*.tar.zst; do
	[ -e "$t" ] || continue
	if zstd -dc "$t" | tar -t >/dev/null 2>&1; then
		echo "OK       $(basename "$t") lists cleanly ($(zstd -dc "$t" | tar -t | wc -l) entries)"
	else
		echo "BAD TAR  $(basename "$t")"; fail=1
	fi
done

grep -q '^QSPI0.img,' "$map" || echo "NOTE     no QSPI0.img in the map (firmware not backed up)"
du -sh "$dir"
[ "$fail" -eq 0 ] && [ "$n" -gt 0 ] && echo "VERIFY: PASS ($n files)" || { echo "VERIFY: FAIL"; exit 1; }
