#!/bin/bash
# Install / update the quota router on a Hermes host. Safe to re-run.
#   bash scripts/install.sh            # install, start in dry-run
# It never switches the router to enforce — do that yourself with `/quota mode enforce`.
# Change reports go to local files by default; QR_DELIVER=telegram bash scripts/install.sh to get them in chat.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ROOT="${QR_HERMES_ROOT:-$HOME/.hermes}"
DEST="$ROOT/plugins/quota-router"
WS="$ROOT/workspace/quota-router"

command -v hermes >/dev/null || { echo "hermes CLI not on PATH"; exit 1; }

# 1. Plugin in place (skip when running from the installed copy)
if [ "$(cd "$HERE" && pwd -P)" != "$(mkdir -p "$DEST" && cd "$DEST" && pwd -P)" ]; then
  rsync -a --delete --exclude .git --exclude __pycache__ "$HERE/" "$DEST/"
  echo "✓ plugin copied to $DEST"
fi

# 2. Cron shim (cron only runs scripts inside $ROOT/scripts)
mkdir -p "$ROOT/scripts"
cp "$DEST/scripts/cron_shim.py" "$ROOT/scripts/quota_router.py"
echo "✓ cron shim at $ROOT/scripts/quota_router.py"

# 3. Workspace + starter policy (never overwrites an existing policy)
mkdir -p "$WS"
if [ ! -f "$WS/policy.yaml" ]; then
  cp "$DEST/examples/policy.example.yaml" "$WS/policy.yaml"
  echo "✓ starter policy written (mode: dry_run) — edit $WS/policy.yaml for your bots"
else
  echo "• existing policy kept"
fi

# 4. Enable the plugin (/quota + dashboard/desktop backend)
hermes plugins enable quota-router >/dev/null && echo "✓ plugin enabled"

# 5. Cron job (created once)
if hermes cron list 2>/dev/null | grep -q "Script:.*quota_router.py"; then
  echo "• cron job already present"
else
  hermes cron create "every 30m" --script quota_router.py --no-agent --name "Quota router" --deliver "${QR_DELIVER:-local}" >/dev/null
  echo "✓ cron job created (every 30 min, zero tokens)"
fi

cat <<EOF

Done. Next:
  • Restart the gateway so /quota and the API routes load:   hermes gateway restart
  • Check it:                                               /quota   (Telegram or CLI)
  • Desktop app: Capabilities → Plugins → Rescan → enable "Quota Router"
  • When the dry-run decisions look right:                  /quota mode enforce
EOF
