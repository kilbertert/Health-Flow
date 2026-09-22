#!/bin/bash
# Assert the session cookie carries the Secure flag and HttpOnly.
set -uo pipefail
BASE=${BASE:-http://127.0.0.1:10007}
EMAIL="cookie-probe-$$@example.invalid"
HDR=$(mktemp)
trap 'rm -f "$HDR"' EXIT

curl -s -D "$HDR" -o /dev/null --max-time 15 \
  -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"probe-password-$$\"}"

line=$(grep -i '^set-cookie' "$HDR" | head -1 | tr -d '\r')
if [ -z "$line" ]; then
  echo "FAIL  no Set-Cookie returned"; exit 1
fi
# Print the attribute names only, never the token value.
attrs=$(echo "$line" | sed 's/^[Ss]et-[Cc]ookie: *[^;]*//' | sed 's/^;//' | tr -d '\r')
echo "attributes:$attrs"

fail=0
echo "$line" | grep -qi 'Secure'   && echo "ok    Secure"   || { echo "FAIL  Secure absent";   fail=1; }
echo "$line" | grep -qi 'HttpOnly' && echo "ok    HttpOnly" || { echo "FAIL  HttpOnly absent"; fail=1; }

[ "$fail" -eq 0 ] && echo "cookie-probe: session cookie hardened" || { echo "cookie-probe: FAILED"; exit 1; }
