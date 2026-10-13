"""Resolve public environment metadata at image-build time, without executing tests."""

import json
from pathlib import Path

from swebench.harness.test_spec.test_spec import make_test_spec

make_test_spec(json.loads(Path("/rtv-build-config.json").read_text()))
