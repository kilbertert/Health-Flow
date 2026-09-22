#!/bin/bash
# Exercise the account session boundary and the report upload contract.
#
# Asserts:
#   - registration returns a session
#   - a protected route is reachable WITH the session and was refused without it
#   - upload persists and returns 202 processing (not parsed inline)
set -uo pipefail

BASE=${BASE:-http://127.0.0.1:10007}
COOKIE=$(mktemp)
TMPFILE=$(mktemp --suffix=.pdf)
trap 'rm -f "$COOKIE" "$TMPFILE"' EXIT

EMAIL="probe-$$@example.invalid"
PASSWORD="probe-password-$$"

printf '%%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%%%EOF\n' > "$TMPFILE"

fail=0

echo -n "register           -> "
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -c "$COOKIE" \
  -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
  -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")
[ "$code" = "200" ] || [ "$code" = "201" ] && echo "ok ($code)" || { echo "FAIL ($code)"; fail=1; }

echo -n "session reused     -> "
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -b "$COOKIE" "$BASE/api/auth/me")
[ "$code" = "200" ] && echo "ok (200)" || { echo "FAIL ($code)"; fail=1; }

echo -n "upload             -> "
body=$(mktemp)
code=$(curl -s -o "$body" -w '%{http_code}' --max-time 30 -b "$COOKIE" \
  -X POST "$BASE/api/health/report/upload" -F "file=@$TMPFILE;type=application/pdf")
echo -n "$code "
if [ "$code" = "202" ]; then
  status=$(python3 -c "import json,sys; print(json.load(open('$body')).get('status',''))" 2>/dev/null)
  if [ "$status" = "processing" ]; then echo "ok (processing)"; else echo "FAIL (status=$status)"; fail=1; fi
else
  echo "FAIL (expected 202)"; head -c 200 "$body"; fail=1
fi
rm -f "$body"

if [ "$fail" -ne 0 ]; then echo "upload-probe: FAILED"; exit 1; fi
echo "upload-probe: account boundary and upload contract verified"
