#!/usr/bin/env bash
# Replace the GitHub repo URL placeholder everywhere in this repo.
# Usage: ./tools/set-repo-url.sh https://github.com/<owner>/<name>
# The current value lives in .repo-url (single source of truth); every tracked
# file containing that value is rewritten, then .repo-url is updated.
set -euo pipefail
cd "$(dirname "$0")/.."
NEW="${1:-}"
if [[ ! "$NEW" =~ ^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$ ]]; then
  echo "Usage: $0 https://github.com/<owner>/<name>   (no trailing slash, no .git)" >&2
  exit 1
fi
OLD="$(tr -d '[:space:]' < .repo-url)"
if [ "$OLD" = "$NEW" ]; then echo "Already set to $NEW"; exit 0; fi
OLD_RAW="${OLD/https:\/\/github.com\//https://raw.githubusercontent.com/}"
NEW_RAW="${NEW/https:\/\/github.com\//https://raw.githubusercontent.com/}"
FILES=$(git ls-files 2>/dev/null || find . -type f -not -path './.git/*')
COUNT=0
for f in $FILES; do
  [ -f "$f" ] || continue
  if grep -qF -e "$OLD" -e "$OLD_RAW" "$f" 2>/dev/null; then
    python3 - "$f" "$OLD" "$NEW" "$OLD_RAW" "$NEW_RAW" <<'PY'
import sys, pathlib
p, old, new, old_raw, new_raw = sys.argv[1:]
t = pathlib.Path(p).read_text(encoding="utf-8")
pathlib.Path(p).write_text(t.replace(old_raw, new_raw).replace(old, new), encoding="utf-8")
PY
    echo "  updated $f"; COUNT=$((COUNT+1))
  fi
done
echo "$NEW" > .repo-url
echo "Done: $COUNT file(s) now point to $NEW"
