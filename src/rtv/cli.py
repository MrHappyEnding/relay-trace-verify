"""RTV command-line entry point."""

import argparse
import asyncio
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
    for name, help_text in [
        ("harbor-doctor", "Check pinned Harbor, task schema and Docker without starting anything"),
        ("harbor-smoke", "Run the model-free Harbor container checkpoint probe"),
    ]:
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--task", type=Path, default=Path("examples/tasks/positive-sum"))
        if name == "harbor-smoke":
            command.add_argument("--output", type=Path, default=Path("work/harbor-smoke"))
    args = parser.parse_args(argv)
    try:
        if args.command.startswith("harbor-"):
            from rtv.harbor_probe import doctor, run_smoke

            if args.command == "harbor-doctor":
                result = doctor(args.task)
                passed = result["ready"]
            else:
                result = asyncio.run(run_smoke(args.task, args.output))
                passed = result["status"] == "passed"
            print(json.dumps(result, indent=2))
            return 0 if passed else 1
        report = run_demo(args.output)
    except (OSError, ValueError) as exc:
        print(f"rtv: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.verification.passed else 1
