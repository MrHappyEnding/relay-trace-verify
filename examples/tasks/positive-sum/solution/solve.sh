#!/bin/bash
set -eu
cat > /workspace/positive_sum.py <<'PY'
def positive_sum(values):
    return sum(value for value in values if value > 0)
PY
