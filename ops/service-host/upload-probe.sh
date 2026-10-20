#!/bin/bash
#
# Assert the account boundary and the report upload contract, from the host
# itself.
#
# Asserts:
#   - a protected route is refused WITHOUT a session (the negative case)
#   - upload is refused WITHOUT a session (the negative case)
#   - a minted session reaches a protected route
#   - upload persists and returns 202 processing (not parsed inline)
#
# **This probe does not create an identity.** Registration and password login
# were retired with the account system (#172); the live deployment answers 405
# for the retired registration route, so a probe that registered would fail for
# a reason that has nothing to do with uploads. The session comes from
# `mint-probe-session.py` — the deployment's existing session-minting tool,
# shipped with the evidence service's acceptance suite — which writes a live
# session row and records the cookie in a state file. What this probe measures
# is "is an upload accepted", and a real session is the correct boundary for
# that question; it does not need a mall ticket.
#
# The session cookie's `Secure` / `HttpOnly` attributes are **not** asserted
# here. They can only be observed on a real ticket exchange (`set_cookie` is not
# on the minting path), so they belong to the ticket-based acceptance instead of
# a fabricated-session probe. A probe that asserted them here would either lie
# or test the mint, not the deployment.
#
# Usage — three steps, and the cleanup is not optional:
#
#     mint-probe-session.py              # writes the state file (mode 0600)
#     upload-probe.sh                    # this script
#     cleanup-probe-session.py           # removes the minted session
#
# The minted session is a live credential for a real account. Leaving it behind
# means a working token exists with nobody accountable for it. `upload-probe.sh`
# never deletes it itself: one script owning both the mint and the revoke would
# hide the window between them.
#
# Exit status is nonzero on any mismatch, so this is a verification rather than
# a report: a deployment that let anonymous callers through would fail here.

set -uo pipefail

BASE=${BASE:-http://127.0.0.1:10007}
STATE=${PROBE_STATE:-/tmp/e2e-state.json}
TMPFILE=$(mktemp --suffix=.pdf)
trap 'rm -f "$TMPFILE"' EXIT

failures=0
check() { # check <label> <predicate-cmd> <actual> <expected>
  local label=$1 actual=$3 expected=$4
  if "$2" "$actual"; then printf 'ok    %-22s %s\n' "$label" "$actual"
  else printf 'FAIL  %-22s got %s, expected %s\n' "$label" "$actual" "$expected" >&2; failures=$((failures+1)); fi
}
is_401() { [ "$1" = "401" ]; }
is_200() { [ "$1" = "200" ]; }
is_202() { [ "$1" = "202" ]; }

printf '%%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%%%EOF\n' > "$TMPFILE"

# --- negative: no session must be refused ---
check "protected (no session)" is_401 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -b "" "$BASE/api/auth/me")" "401"
check "upload (no session)" is_401 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -b "" \
     -X POST "$BASE/api/health/report/upload" -F "file=@$TMPFILE;type=application/pdf")" "401"

# --- session: minted beforehand, consumed from the state file ---
if [ ! -f "$STATE" ]; then
  printf 'FAIL  %-22s %s missing — run mint-probe-session.py first\n' "session state" "$STATE" >&2
  printf '\nupload-probe: 1 case(s) failed (no session, so the upload contract was not observed)\n' >&2
  exit 1
fi
# `curl -b` takes a Netscape cookie jar. A plain `name=value` file sends
# nothing — silently, which presents as an authentication failure rather than
# the test bug it is.
cookiejar=$(mktemp)
trap 'rm -f "$TMPFILE" "$cookiejar"' EXIT
python3 - "$STATE" "$cookiejar" <<'PY'
import json, sys

state_path, jar_path = sys.argv[1], sys.argv[2]
state = json.load(open(state_path))
name = state["cookie_name"]
value = state["cookie"].split("=", 1)[1]
with open(jar_path, "w") as fh:
    fh.write("# Netscape HTTP Cookie File\n")
    fh.write(f"127.0.0.1\tFALSE\t/\tFALSE\t0\t{name}\t{value}\n")
PY
check "protected (session)" is_200 \
  "$(curl -s -o /dev/null -w '%{http_code}' --max-time 15 -b "$cookiejar" "$BASE/api/auth/me")" "200"

# --- upload contract ---
body=$(mktemp)
code=$(curl -s -o "$body" -w '%{http_code}' --max-time 30 -b "$cookiejar" \
  -X POST "$BASE/api/health/report/upload" -F "file=@$TMPFILE;type=application/pdf")
check "upload" is_202 "$code" "202"
status=$(python3 -c "import json;print(json.load(open('$body')).get('status',''))" 2>/dev/null)
job=$(python3 -c "import json;print(json.load(open('$body')).get('extraction_job',{}).get('status',''))" 2>/dev/null)
report_id=$(python3 -c "import json;print(json.load(open('$body')).get('id',''))" 2>/dev/null)
rm -f "$body"
if [ "$status" = "processing" ]; then printf 'ok    %-22s %s\n' "upload status" "processing"
else printf 'FAIL  %-22s got %s, expected processing\n' "upload status" "$status" >&2; failures=$((failures+1)); fi
if [ "$job" = "queued" ]; then printf 'ok    %-22s %s\n' "extraction job" "queued"
else printf 'FAIL  %-22s got %s, expected queued\n' "extraction job" "$job" >&2; failures=$((failures+1)); fi

# The upload created real rows (a report, its file, an extraction job, the
# uploaded page). Delete it through the API as the same session, which removes
# them in one authorized operation. Best effort; a failure is reported rather
# than hidden, because a leftover report is data nobody asked for.
if [ -n "$report_id" ]; then
  del=$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 -b "$cookiejar" \
          -X DELETE "$BASE/api/health/report/$report_id")
  if [ "$del" = "200" ]; then printf 'ok    %-22s %s\n' "cleanup (report)" "deleted"
  else printf 'FAIL  %-22s delete returned %s\n' "cleanup (report)" "$del" >&2; failures=$((failures+1)); fi
fi

[ "$failures" -eq 0 ] || { printf '\nupload-probe: %d case(s) failed\n' "$failures" >&2; exit 1; }
printf '\nupload-probe: account boundary and upload contract verified\n'
printf 'now run cleanup-probe-session.py — the minted session is a live credential\n'
