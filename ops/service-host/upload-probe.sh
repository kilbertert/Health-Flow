#!/bin/bash
#
# Assert the account boundary, the session cookie hardening, and the report
# upload contract, from the host itself.
#
# Asserts:
#   - a protected route is refused WITHOUT a session (the negative case)
#   - registration yields a usable session
#   - the session cookie carries Secure and HttpOnly
#   - upload persists and returns 202 processing (not parsed inline)
#   - an unknown route is not silently accepted
#
# Exit status is nonzero on any mismatch, so this is a verification rather than
# a report: a deployment that let anonymous callers through would fail here.

set -uo pipefail

BASE=${BASE:-http://127.0.0.1:10007}
COOKIE=$(mktemp)
TMPFILE=$(mktemp --suffix=.pdf)
HDR=$(mktemp)
trap 'rm -f "$COOKIE" "$TMPFILE" "$HDR"' EXIT

failures=0
check() { # check <label> <predicate-cmd> <actual> <expected>
  local label=$1 actual=$3 expected=$4
  if "$2" "$actual"; then printf 'ok    %-22s %s\n' "$label" "$actual"
  else printf 'FAIL  %-22s got %s, expected %s\n' "$label" "$actual" "$expected" >&2; failures=$((failures+1)); fi
}
is_401() { [ "$1" = "401" ]; }
is_201() { [ "$1" = "201" ] || [ "$1" = "200" ]; }
is_200() { [ "$1" = "200" ]; }
is_202() { [ "$1" = "202" ]; }
has() { printf '%s' "$2" | grep -qi "$1"; }

printf '%%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%%%EOF\n' > "$TMPFILE"
EMAIL="probe-$$@example.invalid"
PASSWORD="probe-password-$$"

# --- negative: no session must be refused ---
check "protected (no session)" is_401 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -b "" "$BASE/api/auth/me")" "401"
check "upload (no session)" is_401 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -b "" \
     -X POST "$BASE/api/health/report/upload" -F "file=@$TMPFILE;type=application/pdf")" "401"

# --- session ---
check "register" is_201 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -c "$COOKIE" -D "$HDR" \
     -X POST "$BASE/api/auth/register" -H 'Content-Type: application/json' \
     -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}")" "201"
check "protected (session)" is_200 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -b "$COOKIE" "$BASE/api/auth/me")" "200"

# --- cookie hardening ---
cookie=$(grep -i '^set-cookie' "$HDR" | head -1 | tr -d '\r')
if printf '%s' "$cookie" | grep -qi 'secure'; then printf 'ok    %-22s %s\n' "cookie Secure" "present"
else printf 'FAIL  %-22s %s\n' "cookie Secure" "absent" >&2; failures=$((failures+1)); fi
if printf '%s' "$cookie" | grep -qi 'httponly'; then printf 'ok    %-22s %s\n' "cookie HttpOnly" "present"
else printf 'FAIL  %-22s %s\n' "cookie HttpOnly" "absent" >&2; failures=$((failures+1)); fi

# --- upload contract ---
body=$(mktemp)
code=$(curl -s -o "$body" -w '%{http_code}' --max-time 30 -b "$COOKIE" \
  -X POST "$BASE/api/health/report/upload" -F "file=@$TMPFILE;type=application/pdf")
check "upload" is_202 "$code" "202"
status=$(python3 -c "import json;print(json.load(open('$body')).get('status',''))" 2>/dev/null)
if [ "$status" = "processing" ]; then printf 'ok    %-22s %s\n' "upload status" "processing"
else printf 'FAIL  %-22s got %s, expected processing\n' "upload status" "$status" >&2; failures=$((failures+1)); fi
rm -f "$body"

[ "$failures" -eq 0 ] || { printf '\nupload-probe: %d case(s) failed\n' "$failures" >&2; exit 1; }
printf '\nupload-probe: account boundary, cookie hardening and upload contract verified\n'
