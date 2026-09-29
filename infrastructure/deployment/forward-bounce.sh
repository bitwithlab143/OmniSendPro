#!/bin/sh
# Forward one raw bounce (DSN) or complaint (ARF) email from stdin to OmniSendPro (design DS-16).
#
# Use from an MTA pipe transport, e.g. Postfix /etc/aliases:
#   bounces: "|/usr/local/bin/forward-bounce.sh"
# with OMNISEND_INBOUND_URL and OMNISEND_WEBHOOK_SECRET set in the pipe's environment
# (the URL and secret are shown once when the provider's webhook secret is rotated).
#
# Exit codes follow sysexits so the MTA retries (75) on transient failures instead of bouncing.
set -eu

: "${OMNISEND_INBOUND_URL:?set OMNISEND_INBOUND_URL}"
: "${OMNISEND_WEBHOOK_SECRET:?set OMNISEND_WEBHOOK_SECRET}"

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
head -c 1048576 > "$tmp"

ts=$(date +%s)
sig=$( { printf '%s.' "$ts"; cat "$tmp"; } \
  | openssl dgst -sha256 -hmac "$OMNISEND_WEBHOOK_SECRET" -r | cut -d' ' -f1)

code=$(curl -sS -o /dev/null -w '%{http_code}' --max-time 30 \
  -H "Content-Type: message/rfc822" \
  -H "X-OmniSend-Timestamp: $ts" \
  -H "X-OmniSend-Signature: sha256=$sig" \
  --data-binary "@$tmp" "$OMNISEND_INBOUND_URL") || exit 75

case "$code" in
  2??) exit 0 ;;
  401|404|413) echo "omnisend inbound rejected the report: HTTP $code" >&2; exit 69 ;;
  *) exit 75 ;;
esac
