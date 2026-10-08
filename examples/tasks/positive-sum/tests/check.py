"""Task verifier, uploaded after execution rather than baked into the agent image."""

import importlib.util
from pathlib import Path


def check(workspace: Path) -> None:
    spec = importlib.util.spec_from_file_location("candidate", workspace / "positive_sum.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for values, expected in [([], 0), ([1, -2, 3], 4), ([-5, 0], 0), ([2, 7], 9)]:
        actual = module.positive_sum(values)
        if actual != expected:
            raise AssertionError(f"positive_sum({values!r}): expected {expected}, got {actual}")
    if module.positive_sum(iter([1, -4, 2])) != 3:
        raise AssertionError("Iterable inputs must be supported")


if __name__ == "__main__":
    check(Path("/workspace"))
    print("All fixture checks passed")
