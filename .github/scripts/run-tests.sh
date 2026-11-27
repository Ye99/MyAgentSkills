#!/usr/bin/env bash
# Run every skill's test suite, one pytest process per test folder so same-named test
# modules in different skills never collide. Exits non-zero if any suite fails.
# Usage: .github/scripts/run-tests.sh [pytest args...]
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1

mapfile -t dirs < <(
  find . -path ./.git -prune -o -path '*/node_modules' -prune -o -path '*/.venv*' -prune -o \
    -type f \( -name 'test_*.py' -o -name '*_test.py' \) -print |
    sed 's|/[^/]*$||' | sort -u
)

failed=()
for d in "${dirs[@]}"; do
  echo "::group::${d#./}"
  python3 -m pytest -q -p no:cacheprovider "$@" "$d"
  rc=$?
  echo "::endgroup::"
  # 5 = no tests collected, which is not a failure here.
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 5 ]; then
    failed+=("${d#./}")
  fi
done

if [ "${#failed[@]}" -gt 0 ]; then
  printf 'FAILED suites:\n'; printf '  %s\n' "${failed[@]}"
  exit 1
fi
echo "All ${#dirs[@]} suites passed."
