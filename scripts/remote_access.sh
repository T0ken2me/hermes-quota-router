#!/bin/bash
# Put the Hermes dashboard (incl. the Quota Router tab) on your tailnet — HTTPS, login required.
#   bash scripts/remote_access.sh on     # enable
#   bash scripts/remote_access.sh off    # back to local-only
# The dashboard stays bound to 127.0.0.1; Tailscale Serve (tailnet only, NOT Funnel) fronts it.
set -euo pipefail
ROOT="${QR_HERMES_ROOT:-$HOME/.hermes}"
PORT="${QR_DASHBOARD_PORT:-9119}"
TS="$(command -v tailscale || echo /Applications/Tailscale.app/Contents/MacOS/Tailscale)"
LABEL=com.hermes.dashboard
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

case "${1:-}" in
on)
  grep -q '^HERMES_DASHBOARD_BASIC_AUTH_PASSWORD_HASH=' "$ROOT/.env" 2>/dev/null || {
    echo "No dashboard login yet. Run first:  bash $(dirname "$0")/dashboard_set_password.sh"; exit 1; }
  if lsof -iTCP:"$PORT" -sTCP:LISTEN -n -P >/dev/null 2>&1; then
    echo "Something already listens on :$PORT. Stop it first (hermes dashboard --stop) so no"
    echo "unauthenticated dashboard ends up behind Tailscale."; exit 1
  fi
  DNS="$("$TS" status --json | python3 -c 'import json,sys;print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))')"
  hermes config set dashboard.public_url "https://$DNS" >/dev/null
  if [ "$(uname)" = Darwin ]; then
    sed -e "s#__HERMES__#$(command -v hermes)#; s#__ROOT__#$ROOT#g; s#__HOME__#$HOME#g; s#__PORT__#$PORT#; s#__LABEL__#$LABEL#" \
        "$(dirname "$0")/../examples/dashboard.launchd.plist" > "$PLIST"
    plutil -lint "$PLIST" >/dev/null
    launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
    launchctl bootstrap "gui/$(id -u)" "$PLIST"
  else
    echo "Non-macOS: run under your supervisor:  hermes dashboard --host 127.0.0.1 --port $PORT --no-open"
  fi
  # Fail closed: the gate must be on before anything is forwarded. With the login required, the
  # page carries no session token and a protected API answers 401 to an anonymous request.
  ok=""
  for _ in $(seq 1 20); do
    code="$(curl -s -o /dev/null -w '%{http_code}' -m 5 "http://127.0.0.1:$PORT/api/plugins/quota-router/overview" || true)"
    page="$(curl -sL -m 5 "http://127.0.0.1:$PORT/" || true)"
    if [ "$code" = 401 ] && [ -n "$page" ] && ! grep -q "__HERMES_SESSION_TOKEN__" <<<"$page"; then ok=1; break; fi
    sleep 2
  done
  [ -n "$ok" ] || { echo "Dashboard is not answering with the login gate on; NOT exposing it."
                    echo "Check $ROOT/logs/dashboard.err"; exit 1; }
  "$TS" serve --bg --https=443 "http://127.0.0.1:$PORT" >/dev/null
  echo "✓ https://$DNS  (tailnet only, login required)"
  ;;
off)
  "$TS" serve --https=443 off >/dev/null 2>&1 || true
  hermes config unset dashboard.public_url >/dev/null 2>&1 || true
  [ -f "$PLIST" ] && launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  echo "✓ Tailscale Serve off; dashboard back to local-only (start it with: hermes dashboard)"
  ;;
*) echo "usage: $0 on|off"; exit 2 ;;
esac
