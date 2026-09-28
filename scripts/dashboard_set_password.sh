#!/bin/sh
# Hidden-prompt password setup for the Hermes dashboard login (stores a scrypt hash only).
ROOT="${QR_HERMES_ROOT:-$HOME/.hermes}"
exec "$ROOT/hermes-agent/venv/bin/python" "$(dirname "$0")/dashboard_set_password.py"
