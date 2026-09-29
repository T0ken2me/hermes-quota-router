#!/bin/bash
# Run every test. They use temporary copies of the example policy and cannot touch a live install.
set -u
cd "$(dirname "$0")"
PY="${HERMES_PY:-$HOME/.hermes/hermes-agent/venv/bin/python}"
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"
rc=0
for t in test_ladder test_security test_api test_alert test_spend; do
  echo "### $t"
  "$PY" "$t.py" || rc=1
done
exit $rc
