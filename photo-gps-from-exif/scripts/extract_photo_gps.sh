#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  printf 'Usage: %s <file-or-directory>\n' "$(basename "$0")" >&2
  exit 2
fi

target="$1"

if ! command -v exiftool >/dev/null 2>&1; then
  printf 'Error: exiftool not found. Install it first.\n' >&2
  exit 1
fi

if [[ ! -e "$target" ]]; then
  printf 'Error: path does not exist: %s\n' "$target" >&2
  exit 1
fi

tmp_output="$(mktemp)"
trap 'rm -f "$tmp_output"' EXIT

if [[ -d "$target" ]]; then
  exiftool -r -ext jpg -ext jpeg -ext heic -ext mov -n -FileName -GPSLatitude -GPSLongitude "$target" >"$tmp_output"
else
  exiftool -n -FileName -GPSLatitude -GPSLongitude "$target" >"$tmp_output"
fi

python3 - "$tmp_output" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
raw = path.read_text(encoding='utf-8', errors='replace').splitlines()

records = []
current = {}
for line in raw:
    if line.startswith('======== '):
        if current:
            records.append(current)
        current = {'Source File': line.replace('======== ', '', 1).strip()}
        continue
    if line.startswith('    ') and ('files read' in line or 'directories scanned' in line):
        continue
    if not line.strip():
        if current:
            records.append(current)
            current = {}
        continue
    if ':' not in line:
        continue
    key, value = line.split(':', 1)
    current[key.strip()] = value.strip()
if current:
    records.append(current)

with_gps = []
without_gps = []

for rec in records:
    file_name = rec.get('Source File') or rec.get('File Name') or '(unknown)'
    lat = rec.get('GPS Latitude')
    lon = rec.get('GPS Longitude')
    if lat is not None and lon is not None:
        with_gps.append((file_name, lat, lon))
    else:
        without_gps.append(file_name)

print('Files with GPS:')
if with_gps:
    for file_name, lat, lon in with_gps:
        print(f'- {file_name}: {lat}, {lon}')
else:
    print('- none')

print('\nFiles without GPS:')
if without_gps:
    for file_name in without_gps:
        print(f'- {file_name}')
else:
    print('- none')
PY
