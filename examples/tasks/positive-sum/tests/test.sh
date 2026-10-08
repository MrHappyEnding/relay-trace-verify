#!/bin/bash
set -eu
mkdir -p /logs/verifier
# Reset first: a previous successful reward must not survive a failing rerun.
printf '0\n' > /logs/verifier/reward.txt
if python /tests/check.py; then
    printf '1\n' > /logs/verifier/reward.txt
fi
