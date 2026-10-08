#!/usr/bin/env bash
# Bounded multi-stream test: N parallel DIRECT-PLAY style downloads of one item for a limited time.
# Run it from a machine OUTSIDE the server (home PC, or a friend's) to measure the real path.
#
#   BASE_URL=https://media.example.com ITEM_ID=<id> API_KEY=<key> STREAMS=4 DURATION=30 bash stream-test.sh
#
# ITEM_ID: open the item in the web UI; the id is in the URL (...details?id=<ITEM_ID>).
# API_KEY: Dashboard -> API Keys. Create a temporary key and DELETE it after the test.
set -euo pipefail
: "${BASE_URL:?}"; : "${ITEM_ID:?}"; : "${API_KEY:?}"
STREAMS="${STREAMS:-4}"; SECONDS_LIMIT="${DURATION:-30}"

echo "Running $STREAMS parallel streams for ${SECONDS_LIMIT}s against $BASE_URL (read only)..."
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
for i in $(seq 1 "$STREAMS"); do
  (
    # Header auth keeps the key out of the URL (and out of proxy logs).
    curl -s -o /dev/null -m "$SECONDS_LIMIT" \
      -H "Authorization: MediaBrowser Token=\"$API_KEY\"" \
      -w '%{speed_download}\n' "$BASE_URL/Videos/$ITEM_ID/stream?static=true" > "$TMP/$i" || true
  ) &
done
wait

TOTAL=0
for i in $(seq 1 "$STREAMS"); do
  BPS="$(cat "$TMP/$i")"
  MBIT="$(awk -v b="$BPS" 'BEGIN{printf "%.1f", b*8/1000000}')"
  echo "stream $i: ${MBIT} Mbit/s"
  TOTAL="$(awk -v t="$TOTAL" -v m="$MBIT" 'BEGIN{print t+m}')"
done
echo "aggregate: ${TOTAL} Mbit/s"
echo "Rule of thumb: each 1080p stream needs ~8-15 Mbit/s sustained. Per-stream speed above that = OK."
echo "Delete the temporary API key now."
