#!/usr/bin/env bash
# Simulate the WhatsApp integration calling the care-bot API.
#
#   scripts/simulate.sh            # walk through every route and the state rules
#   scripts/simulate.sh load 10    # 10 bundle questions at once from different numbers
#
# Start the API first (needs CARE_DATABASE_URL, see README):
#   CARE_API_TOKEN=dev uvicorn care.api:create_app --factory
# Env: BASE_URL (default http://localhost:8000), CARE_API_TOKEN (default dev).
# Needs curl and jq.
set -euo pipefail

BASE_URL=${BASE_URL:-http://localhost:8000}
TOKEN=${CARE_API_TOKEN:-dev}
MSISDN=263772000001        # the fake backend has a balance for this number
AREA=harare-cbd            # and a planned outage for this area
RUN=$(date +%s)            # keeps message IDs unique across runs
N=0

for cmd in curl jq; do
    command -v "$cmd" >/dev/null || { echo "needs $cmd" >&2; exit 1; }
done
curl -sf "$BASE_URL/health" >/dev/null || { echo "API not reachable at $BASE_URL" >&2; exit 1; }

# send TEXT [MESSAGE_ID] [MSISDN] -> prints the outcome and time taken
send() {
    local text=$1 id=${2:-} msisdn=${3:-$MSISDN}
    if [[ -z $id ]]; then N=$((N + 1)); id="wamid.sim-$RUN-$N"; fi
    local body out code secs
    body=$(jq -nc --arg id "$id" --arg m "$msisdn" --arg a "$AREA" --arg t "$text" \
        '{message_id: $id, msisdn: $m, area: $a, text: $t}')
    out=$(curl -s -w '\n%{http_code} %{time_total}' -X POST "$BASE_URL/messages" \
        -H "Authorization: Bearer $TOKEN" -H 'content-type: application/json' -d "$body")
    read -r code secs <<<"$(tail -n1 <<<"$out")"
    out=$(sed '$d' <<<"$out")
    if [[ $code == 200 ]]; then
        out=$(jq -r --arg s "$secs" '"  [\(.status) route=\(.route) handoff=\(.handoff) dup=\(.duplicate) blocked=\(.blocked) \($s)s]\n  \(.reply // "(no reply: agent owns the conversation)")"' <<<"$out")
    else
        out="  HTTP $code ${secs}s: $(jq -r '.detail | if type == "array" then map(.msg) | join("; ") else . end' <<<"$out" 2>/dev/null || echo "$out")"
    fi
    # One write per call, so parallel calls in load mode don't interleave.
    printf '> %s\n%s\n' "${text:0:80}" "$out"
}

release() {
    curl -s -X POST "$BASE_URL/conversations/$1/release" -H "Authorization: Bearer $TOKEN" \
        | jq -r '"  [released: status=\(.status)]"'
}

# Each section starts with the bot owning the conversation, so an earlier
# handoff doesn't silence it.
section() {
    printf '\n== %s ==\n' "$1"
    curl -s -X POST "$BASE_URL/conversations/$MSISDN/release" -H "Authorization: Bearer $TOKEN" >/dev/null
}

scenario() {
    section "one message per route"
    send "What is my balance?"
    send "How much is the 5GB bundle?"
    send "Is the network down in my area?"
    send "My calls keep dropping"
    send "Tell me a joke"            # "other" hands off, so it goes last

    section "WhatsApp redelivers a message: same result, not processed again"
    local id="wamid.sim-$RUN-redeliver"
    send "No signal since this morning" "$id"
    send "No signal since this morning" "$id"

    section "high-risk message hands off; bot then stays quiet"
    send "I sent money to the wrong number, please reverse it"
    send "Hello? What is my balance?"

    section "agent releases the conversation; bot answers again"
    release "$MSISDN"
    send "What is my balance?"

    section "conversation state"
    curl -s "$BASE_URL/conversations/$MSISDN" -H "Authorization: Bearer $TOKEN" \
        | jq '{status, reason, updated_at, last_messages: [.messages[:5][] | {message_id, route, handoff}]}'

    section "rejected requests"
    TOKEN=wrong send "What is my balance?"
    send "$(printf 'x%.0s' {1..4100})"
    send "What is my balance?" "" "not-a-number"
}

load() {
    local n=${1:-10} start
    section "$n bundle questions in parallel, one number each"
    start=$(date +%s%N)
    for i in $(seq 1 "$n"); do
        send "How much is the 1GB daily bundle?" "wamid.load-$RUN-$i" "26377100$(printf '%04d' "$i")" &
    done
    wait
    printf '\n%s messages in %ss\n' "$n" "$(( ($(date +%s%N) - start) / 1000000000 ))"
}

case ${1:-scenario} in
    scenario) scenario ;;
    load) load "${2:-10}" ;;
    *) echo "usage: $0 [scenario | load N]" >&2; exit 2 ;;
esac
