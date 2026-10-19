#!/bin/bash
# Portable isolated tests: no installed Hermes, real accounts or provider calls.
set -eu
cd "$(dirname "$0")"
PY="$(command -v "${HERMES_PY:-python3}")"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/qr-suite.XXXXXX")"
trap 'rm -rf "$SANDBOX"' EXIT
rc=0
for test in test_*.py; do
  echo "### ${test%.py}"
  env -i PATH="$PATH" HOME="$SANDBOX" TMPDIR="$SANDBOX" \
    HERMES_HOME="$SANDBOX" QR_HERMES_ROOT="$SANDBOX" \
    HERMES_DISABLE_LAZY_INSTALLS=1 PYTHONPATH="$PWD" \
    PYTHONIOENCODING=utf-8 "$PY" "$test" || rc=1
done
exit "$rc"
