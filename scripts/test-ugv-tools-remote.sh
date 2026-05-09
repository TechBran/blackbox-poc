#!/usr/bin/env bash
# test-ugv-tools-remote.sh — verify UGV Beast tool schema API is reachable from BlackBox.
# Works over LAN (default 192.168.1.155) or Tailscale (set UGV_HOST=<tailnet-name>).
set -euo pipefail

UGV_HOST="${UGV_HOST:-192.168.1.155}"
UGV_PORT="${UGV_PORT:-8080}"
BASE="http://${UGV_HOST}:${UGV_PORT}"

pass() { printf '  \033[32m✓\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; exit 1; }
hdr()  { printf '\n\033[1m== %s ==\033[0m\n' "$*"; }

hdr "Target: $BASE"

hdr "1. /health"
HEALTH=$(curl -fsS --max-time 5 "$BASE/health") || fail "health unreachable"
echo "  $HEALTH"
echo "$HEALTH" | grep -q '"ok":true'     && pass "ok:true"   || fail "ok not true"
echo "$HEALTH" | grep -q '"bridge":true' && pass "bridge:true" || fail "bridge not running"

hdr "2. /tools (anthropic format)"
TOOLS_JSON=$(curl -fsS --max-time 5 "$BASE/tools?format=anthropic")
COUNT=$(echo "$TOOLS_JSON" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
echo "  $COUNT tools"
[[ "$COUNT" -ge 20 ]] && pass "tool count $COUNT ≥ 20" || fail "too few tools"
echo "$TOOLS_JSON" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert all("input_schema" in t for t in d)' \
  && pass "all tools have input_schema" || fail "missing input_schema"

hdr "3. /tools (openai format)"
curl -fsS --max-time 5 "$BASE/tools?format=openai" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert all(t["type"]=="function" for t in d)' \
  && pass "all OpenAI tools have type=function" || fail "OpenAI format invalid"

hdr "4. /tools (gemini format)"
curl -fsS --max-time 5 "$BASE/tools?format=gemini" | \
  python3 -c 'import json,sys; d=json.load(sys.stdin); assert all("parameters" in t for t in d)' \
  && pass "all Gemini tools have parameters" || fail "Gemini format invalid"

hdr "5. POST /tool/status_health (full health report)"
HEALTH_TOOL=$(curl -fsS --max-time 5 -X POST "$BASE/tool/status_health" \
  -H 'Content-Type: application/json' -d '{}')
echo "  $HEALTH_TOOL" | python3 -m json.tool | head -20
echo "$HEALTH_TOOL" | grep -q '"bridge_running":true' && pass "bridge_running" || fail "bridge_running missing"

hdr "6. POST /tool/status_get_pose"
curl -fsS --max-time 5 -X POST "$BASE/tool/status_get_pose" \
  -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool

hdr "7. /snapshot/pantilt"
curl -fsS --max-time 5 -o /tmp/ugv_remote_pt.jpg "$BASE/snapshot/pantilt" \
  && pass "downloaded pantilt snapshot" || fail "pantilt snapshot failed"
file /tmp/ugv_remote_pt.jpg | grep -q JPEG && pass "is a valid JPEG" || fail "not JPEG"

hdr "8. /snapshot/oakd"
curl -fsS --max-time 5 -o /tmp/ugv_remote_oak.jpg "$BASE/snapshot/oakd" \
  && pass "downloaded oakd snapshot" || fail "oakd snapshot failed"
file /tmp/ugv_remote_oak.jpg | grep -q JPEG && pass "is a valid JPEG" || fail "not JPEG"

hdr "9. POST /tool/nav_status (Nav2 action client probe)"
curl -fsS --max-time 5 -X POST "$BASE/tool/nav_status" \
  -H 'Content-Type: application/json' -d '{}' | python3 -m json.tool

hdr "ALL CHECKS PASSED"
echo "Base: $BASE"
echo "Snapshots: /tmp/ugv_remote_pt.jpg /tmp/ugv_remote_oak.jpg"
