#!/usr/bin/env bash
# ugv-voice-demo.sh — End-to-end voice-loop demo/check for the UGV Beast.
#
# Verifies the full wake-word → STT → chat → auto-speak chain:
#   1. Three Jetson services active (ugv-tools-api, ugv-voice, ugv-ears)
#   2. BlackBox Orchestrator reachable
#   3. /tools?format=anthropic returns 22 UGV tools
#   4. /speak round-trip (BlackBox /tts → JBL) works
#   5. Tails `journalctl -u ugv-ears` for 60s and reports if the wake phrase
#      "Black Box Flight Recorder" fires the full loop
#
# Exits 0 on all-green, non-zero on any failure.
#
# Env:
#   UGV_HOST       LAN IP or tailnet name of the Jetson (default 192.168.1.155)
#   UGV_USER       host SSH user (default jetson)
#   UGV_SSH_PASS   host SSH password (default jetson)
#   UGV_TOOLS_PORT tools_api port (default 8080)
#   UGV_VOICE_PORT voice speak_server port (default 8081)
#   BLACKBOX_URL   orchestrator URL (default http://100.74.17.54:9091)
#   WAIT_SECONDS   journal-tail window waiting for the wake phrase (default 60)
#   SKIP_WAKE      set to 1 to skip the interactive wake-phrase portion

set -uo pipefail

UGV_HOST="${UGV_HOST:-192.168.1.155}"
UGV_USER="${UGV_USER:-jetson}"
UGV_SSH_PASS="${UGV_SSH_PASS:-jetson}"
UGV_TOOLS_PORT="${UGV_TOOLS_PORT:-8080}"
UGV_VOICE_PORT="${UGV_VOICE_PORT:-8081}"
BLACKBOX_URL="${BLACKBOX_URL:-http://100.74.17.54:9091}"
WAIT_SECONDS="${WAIT_SECONDS:-60}"
SKIP_WAKE="${SKIP_WAKE:-0}"

TOOLS_BASE="http://${UGV_HOST}:${UGV_TOOLS_PORT}"
VOICE_BASE="http://${UGV_HOST}:${UGV_VOICE_PORT}"

# ----- tty helpers -----
if [[ -t 1 ]]; then
  BOLD=$'\033[1m'; GREEN=$'\033[32m'; RED=$'\033[31m'; YEL=$'\033[33m'; DIM=$'\033[2m'; RST=$'\033[0m'
else
  BOLD=""; GREEN=""; RED=""; YEL=""; DIM=""; RST=""
fi
pass() { printf '  %s✓%s %s\n' "$GREEN" "$RST" "$*"; }
fail() { printf '  %s✗%s %s\n' "$RED" "$RST" "$*"; FAIL_COUNT=$((FAIL_COUNT+1)); }
warn() { printf '  %s!%s %s\n' "$YEL" "$RST" "$*"; }
hdr()  { printf '\n%s== %s ==%s\n' "$BOLD" "$*" "$RST"; }
now()  { date '+%H:%M:%S'; }

FAIL_COUNT=0

jetson_ssh() {
  # Run a command on the Jetson host via sshpass+ssh. Stderr merged into stdout.
  sshpass -p "$UGV_SSH_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    -o LogLevel=ERROR "${UGV_USER}@${UGV_HOST}" "$@" 2>&1
}

jetson_sudo() {
  # Run a sudo command on the Jetson host. Password piped via `sudo -S` so no tty.
  sshpass -p "$UGV_SSH_PASS" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
    -o LogLevel=ERROR "${UGV_USER}@${UGV_HOST}" "echo '$UGV_SSH_PASS' | sudo -S -p '' $*" 2>&1
}

# ----- banner -----
echo "${BOLD}UGV Beast — Voice Loop E2E Demo${RST}"
echo "${DIM}host=${UGV_HOST}  tools=${UGV_TOOLS_PORT}  voice=${UGV_VOICE_PORT}  bb=${BLACKBOX_URL}${RST}"
echo "${DIM}started $(now)${RST}"

# ----- 1. Jetson services active -----
hdr "1. Jetson systemd services"
SERVICE_OUT=$(jetson_ssh 'systemctl is-active ugv-tools-api.service ugv-voice.service ugv-ears.service' || true)
mapfile -t STATES <<<"$SERVICE_OUT"
for i in 0 1 2; do
  svc_names=(ugv-tools-api ugv-voice ugv-ears)
  st="${STATES[$i]:-unknown}"
  if [[ "$st" == "active" ]]; then
    pass "${svc_names[$i]}.service  = active"
  else
    fail "${svc_names[$i]}.service  = $st"
  fi
done

# ----- 2. BlackBox reachable -----
hdr "2. BlackBox reachability"
if curl -fsS --max-time 6 "${BLACKBOX_URL}/docs" -o /dev/null; then
  pass "BlackBox /docs responds (${BLACKBOX_URL})"
else
  fail "BlackBox unreachable at ${BLACKBOX_URL}"
fi

# voice speak_server reports its own view of BlackBox reachability
VH=$(curl -fsS --max-time 6 "${VOICE_BASE}/health" || echo '{}')
if echo "$VH" | grep -q '"blackbox_reachable":true'; then
  pass "ugv-voice reports blackbox_reachable:true"
else
  # Show the error field to make diagnosis obvious.
  ERR=$(echo "$VH" | python3 -c 'import sys,json;d=json.load(sys.stdin) if sys.stdin.read() else {}' 2>/dev/null || true)
  fail "ugv-voice reports blackbox_reachable:false  body=$VH"
fi

# ----- 3. 22 UGV tools exposed -----
hdr "3. /tools?format=anthropic returns 22 tools"
TOOLS_JSON=$(curl -fsS --max-time 5 "${TOOLS_BASE}/tools?format=anthropic" || echo "[]")
COUNT=$(echo "$TOOLS_JSON" | python3 -c 'import json,sys
try: print(len(json.load(sys.stdin)))
except Exception: print(0)')
if [[ "$COUNT" == "22" ]]; then
  pass "22 tools registered"
else
  fail "expected 22 tools, got ${COUNT}"
fi
# Spot-check a few canonical names
for want in motion_stop gimbal_reset nav_status status_health; do
  if echo "$TOOLS_JSON" | grep -q "\"name\":\"${want}\""; then
    pass "tool present: ${want}"
  else
    fail "tool missing: ${want}"
  fi
done

# ----- 4. Direct /speak round-trip -----
hdr "4. Direct /speak test (BlackBox /tts → JBL)"
SPEAK_PHRASE="Voice loop is ready. Wake me with Black Box Flight Recorder."
SPEAK_BODY=$(printf '{"text":"%s"}' "$SPEAK_PHRASE")

# Capture HTTP status + body separately so we can surface real errors
# (mpg123 exit codes, ALSA-busy, BlackBox /tts 502, etc). /speak is synchronous
# and blocks until playback completes — budget is chars/15 + 10s.
# One retry with 3s gap: the JBL can be briefly held by a prior speak (ears-
# triggered auto-speak or a recent manual test).
do_speak() {
  curl -sS -o /tmp/.ugv_speak_body -w '%{http_code}' --max-time 35 \
    -X POST -H 'Content-Type: application/json' \
    -d "$SPEAK_BODY" "${VOICE_BASE}/speak" 2>/tmp/.ugv_speak_err
}

SPEAK_T0=$(date +%s)
SPEAK_CODE=$(do_speak || echo "000")
SPEAK_BODY_OUT=$(cat /tmp/.ugv_speak_body 2>/dev/null || true)
if [[ "$SPEAK_CODE" != "200" ]]; then
  warn "/speak HTTP $SPEAK_CODE on first try — retrying in 3s (JBL may be busy from a prior play)"
  sleep 3
  SPEAK_CODE=$(do_speak || echo "000")
  SPEAK_BODY_OUT=$(cat /tmp/.ugv_speak_body 2>/dev/null || true)
fi
SPEAK_T1=$(date +%s)
SPEAK_DUR=$((SPEAK_T1 - SPEAK_T0))

if [[ "$SPEAK_CODE" == "200" ]] && echo "$SPEAK_BODY_OUT" | grep -q '"played":true'; then
  pass "/speak 200 OK in ${SPEAK_DUR}s  $(echo "$SPEAK_BODY_OUT" | head -c 200)"
else
  fail "/speak HTTP ${SPEAK_CODE} after ${SPEAK_DUR}s  body=$SPEAK_BODY_OUT  err=$(cat /tmp/.ugv_speak_err 2>/dev/null)"
fi
rm -f /tmp/.ugv_speak_body /tmp/.ugv_speak_err

# Bail early if anything failed — no point waiting for wake-phrase.
if [[ "$FAIL_COUNT" -gt 0 ]]; then
  hdr "RESULT: ${RED}FAIL${RST} (${FAIL_COUNT} check${FAIL_COUNT/1/})"
  echo "Skipping wake-phrase tail — fix the above first."
  exit 1
fi

# ----- 5. Wake-phrase tail -----
if [[ "$SKIP_WAKE" == "1" ]]; then
  hdr "5. Wake-phrase tail SKIPPED (SKIP_WAKE=1)"
  hdr "RESULT: ${GREEN}GREEN${RST} (pre-wake checks only)"
  exit 0
fi

hdr "5. Tail ugv-ears journal for ${WAIT_SECONDS}s"
echo "${BOLD}${YEL}Now say:${RST}  ${BOLD}\"Black Box Flight Recorder, <your command>\"${RST}"
echo "${DIM}Example: \"Black Box Flight Recorder, what do you see?\"${RST}"
echo "${DIM}Watching for: wake → STT → /chat → auto-speak${RST}"
echo

TAIL_LOG=$(mktemp)
# Follow mode + --since=now so we only see events during this window.
# The journalctl command runs via SSH; we kill it after WAIT_SECONDS.
jetson_sudo "journalctl -u ugv-ears.service -n 0 -f --no-pager --since=now" > "$TAIL_LOG" 2>&1 &
TAIL_PID=$!
trap 'kill $TAIL_PID 2>/dev/null || true; rm -f "$TAIL_LOG"' EXIT

# Show the tail live + timestamp each line as it arrives.
tail -n 0 -F "$TAIL_LOG" 2>/dev/null | \
  while IFS= read -r line; do
    printf '  [%s] %s\n' "$(now)" "$line"
  done &
TAIL_VIEW_PID=$!

sleep "$WAIT_SECONDS"
kill "$TAIL_PID" 2>/dev/null || true
kill "$TAIL_VIEW_PID" 2>/dev/null || true
wait 2>/dev/null || true

# Analyse what we captured.
hdr "6. E2E timeline"
SAW_WAKE=0; SAW_STT=0; SAW_CHAT=0; SAW_SPEAK=0
grep -qiE 'wake(word)?.*(detect|fired|triggered)|wakeword score' "$TAIL_LOG" && SAW_WAKE=1
grep -qiE 'stt|whisper|transcri' "$TAIL_LOG" && SAW_STT=1
grep -qiE '/chat|chat_response|claude' "$TAIL_LOG" && SAW_CHAT=1
grep -qiE 'speak|auto.?speak|spoke' "$TAIL_LOG" && SAW_SPEAK=1

[[ "$SAW_WAKE"  == "1" ]] && pass "wake word fired"        || warn "no wake-word event captured in ${WAIT_SECONDS}s"
[[ "$SAW_STT"   == "1" ]] && pass "STT transcription seen" || warn "no STT event captured"
[[ "$SAW_CHAT"  == "1" ]] && pass "/chat request seen"     || warn "no /chat event captured"
[[ "$SAW_SPEAK" == "1" ]] && pass "auto-speak triggered"   || warn "no speak event captured"

echo
echo "${DIM}Captured log: $TAIL_LOG${RST}"
trap - EXIT  # keep the log around for inspection

if [[ "$SAW_WAKE" == "1" && "$SAW_SPEAK" == "1" ]]; then
  hdr "RESULT: ${GREEN}GREEN${RST} — full voice loop verified"
  exit 0
fi

hdr "RESULT: ${YEL}PARTIAL${RST} — pre-wake checks green, wake-phrase not observed"
echo "The infrastructure is healthy. If you spoke the phrase and nothing fired,"
echo "re-run with WAIT_SECONDS=120 or inspect ${TAIL_LOG} and:"
echo "  sshpass -p '${UGV_SSH_PASS}' ssh ${UGV_USER}@${UGV_HOST} \\"
echo "    'sudo journalctl -u ugv-ears.service -n 200 --no-pager'"
exit 2
