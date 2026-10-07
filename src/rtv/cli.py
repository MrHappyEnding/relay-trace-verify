"""RTV command-line entry point."""

import argparse
import json
import sys
from pathlib import Path

from rtv import __version__
from rtv.demo import run_demo


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rtv", description="Relay Trace Verify local framework")
    parser.add_argument("--version", action="version", version=f"RTV {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run a deterministic local relay without model calls")
    demo.add_argument(
        "--output",
        type=Path,
        default=Path("work/rtv-demo"),
        help="New output directory; existing directories are never overwritten",
    )
    args = parser.parse_args(argv)
    try:
        report = run_demo(args.output)
    except (OSError, ValueError) as exc:
        print(f"rtv: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.verification.passed else 1
