"""Offline protocol inspection and analysis; intentionally no model-launch command."""

import json
from pathlib import Path

from rtv.benchmark.protocol import DEFAULT_PLAN, load, matrix, reservation_plan


def add_parser(commands):
    parser = commands.add_parser("benchmark", help="Frozen SWE-bench selection and paired analysis")
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    sub = parser.add_subparsers(dest="benchmark_command", required=True)
    for name in ("plan", "schedule", "budget"):
        command = sub.add_parser(name)
        if name != "plan":
            command.add_argument("--split", choices=["pilot", "evaluation"], required=True)
    audit = sub.add_parser("audit")
    audit.add_argument("--upstream", type=Path)
    materialize = sub.add_parser("materialize")
    materialize.add_argument("--output", type=Path, required=True)
    prepare = sub.add_parser("prepare-task", help="Bind a passed pilot preflight to local images")
    prepare.add_argument("--upstream", type=Path, required=True)
    prepare.add_argument("--task", required=True)
    prepare.add_argument("--preflight", type=Path, required=True)
    prepare.add_argument("--agent-image")
    prepare.add_argument("--output", type=Path, required=True)
    collect = sub.add_parser(
        "collect-run", help="Derive pilot observations from frozen runtime assignments"
    )
    collect.add_argument("--run", type=Path, required=True)
    collect.add_argument("--output", type=Path, required=True)
    analyze = sub.add_parser("analyze")
    analyze.add_argument("--split", choices=["pilot", "evaluation"], required=True)
    analyze.add_argument("--mode", choices=["scripted", "oracle", "real-model"], required=True)
    analyze.add_argument("--records", type=Path, required=True)
    analyze.add_argument("--output", type=Path, required=True)


def run(args):
    plan = load(args.plan)
    if args.benchmark_command == "collect-run":
        from rtv.benchmark.execution import collect
        from rtv.runtime.config import Settings
        from rtv.runtime.store import Store

        result = collect(
            plan, json.loads(args.run.read_text()), Store(Settings.load()), args.output
        )
        result = {k: v for k, v in result.items() if k != "observations"}
    elif args.benchmark_command == "prepare-task":
        from rtv.runtime.tasks import prepare_contract

        result = prepare_contract(
            args.plan, args.upstream, args.task, args.preflight, args.agent_image
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x") as stream:
            stream.write(json.dumps(result, indent=2) + "\n")
    elif args.benchmark_command in {"audit", "materialize"}:
        from rtv.benchmark.source import audit, catalog_for, materialize

        if args.benchmark_command == "materialize":
            materialize(plan, args.output)
            result = audit(plan, catalog_for(args.plan), args.output)
        else:
            result = audit(plan, catalog_for(args.plan), args.upstream)
    elif args.benchmark_command == "plan":
        result = plan
    elif args.benchmark_command == "schedule":
        result = matrix(plan, args.split)
    elif args.benchmark_command == "budget":
        result = reservation_plan(plan, args.split)
    else:
        from rtv.benchmark.analysis import analyze
        from rtv.benchmark.report import export

        records = [
            json.loads(line) for line in args.records.read_text().splitlines() if line.strip()
        ]
        result = analyze(plan, records, args.split, mode=args.mode)
        export(result, args.output, plan=plan, records=args.records)
        result = {k: v for k, v in result.items() if k != "observations"}
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
