"""Opt-in M3 CLI. Config and auth stay outside durable Workflow payloads."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

from rtv.codex_agent import DEFAULT_MODEL
from rtv.harbor_probe import _docker, tree_digest
from rtv.runtime.config import Settings, task_path


def add_parser(commands):
    parser = commands.add_parser("runtime", help="Local Temporal/PostgreSQL M3 runtime")
    sub = parser.add_subparsers(dest="runtime_command", required=True)
    sub.add_parser("init", help="Initialize PostgreSQL metadata tables")
    prepare = sub.add_parser("prepare", help="Build a local fixture image without model calls")
    prepare.add_argument("--adapter", choices=["fixture", "codex"], default="fixture")
    worker = sub.add_parser("worker", help="Run the local control worker and reconciler")
    worker.add_argument("--allow-model-calls", action="store_true")
    worker.add_argument("--auth-file", type=Path)
    worker.add_argument("--allow-api-calls", action="store_true")
    worker.add_argument("--api-key-file", type=Path)
    submit = sub.add_parser("submit", help="Idempotently submit one trusted coding fixture")
    submit.add_argument("--key", required=True)
    submit.add_argument(
        "--adapter", choices=["fixture", "mini-fixture", "codex", "mini"], default="fixture"
    )
    submit.add_argument("--task-contract", type=Path)
    submit.add_argument(
        "--scripted-controls", type=Path, help="Explicit model-free developer controls"
    )
    submit.add_argument("--allow-api-calls", action="store_true")
    submit.add_argument("--api-call-limit", type=int, default=8)
    submit.add_argument("--api-budget-usd", type=float, default=0)
    submit.add_argument("--agent-seconds", type=int, default=300)
    submit.add_argument("--image", help="Prepared local image tag or immutable ID")
    submit.add_argument("--ttl", type=int, default=300)
    submit.add_argument("--delay", type=int, default=0, help="Fixture-only fault-test delay")
    submit.add_argument("--model")
    submit.add_argument("--allow-model-calls", action="store_true")
    submit.add_argument("--pause-after-checkpoint", action="store_true")
    submit.add_argument(
        "--staged", action="store_true", help="Checkpoint a partial stage, then finish"
    )
    submit.add_argument(
        "--max-recoveries",
        type=int,
        default=0,
        help="Explicit allowance for re-execution after a lost stage",
    )
    for name in ("status", "cancel", "events", "ledger", "history", "retire"):
        command = sub.add_parser(name)
        command.add_argument("trial_id")
    sub.add_parser("reconcile", help="Retry orphan/TTL cleanup without launching new turns")
    recover = sub.add_parser(
        "recover", help="Fence the old execution and recover its last checkpoint"
    )
    recover.add_argument("trial_id")
    recover.add_argument("--key", required=True)
    gc = sub.add_parser("gc", help="Collect unreferenced checkpoint uploads after retention")
    gc.add_argument("--older-than", type=int, default=3600)
    for name in ("pin", "unpin"):
        ref = sub.add_parser(name)
        ref.add_argument("checkpoint_id")
        ref.add_argument("--owner", required=True)

    handoff = sub.add_parser("handoff", help="Prepare and transfer to a new agent Attempt")
    fork = sub.add_parser("fork", help="Freeze paired branches from the same sealed checkpoint")
    for command in (handoff, fork):
        command.add_argument("trial_id")
        command.add_argument("--key", required=True)
        command.add_argument(
            "--adapter",
            choices=["fixture", "codex", "mini", "mini-fixture"],
            default="mini-fixture",
        )
        command.add_argument("--model")
        command.add_argument("--allow-api-calls", action="store_true")
        command.add_argument("--allow-model-calls", action="store_true")
    handoff.add_argument(
        "--context", choices=["native", "task-only", "summary", "portable"], default="summary"
    )
    fork.add_argument("--portable", action="store_true")
    fork.add_argument("--ttl", type=int, default=300)
    experiment = sub.add_parser(
        "experiment", help="Paired branch status, incremental and prefix cost"
    )
    experiment.add_argument("experiment_id")
    sub.add_parser("experiments")
    serve = sub.add_parser("serve", help="Read-only local experiment viewer")
    serve.add_argument("--port", type=int, default=8765)


def make_spec(args) -> dict:
    if getattr(args, "task_contract", None):
        from rtv.runtime.tasks import make_repository_spec

        return make_repository_spec(args)
    if args.adapter in {"mini", "mini-fixture"}:
        raise ValueError("Standalone mini submission requires a repository task contract")
    recoveries = getattr(args, "max_recoveries", 0)
    if getattr(args, "pause_after_checkpoint", False) and not getattr(args, "staged", False):
        raise ValueError("Pausing requires --staged")
    if not 0 <= recoveries <= 3:
        raise ValueError("Recovery allowance must be 0–3")
    if not 1 <= args.ttl <= 1800 or not 0 <= args.delay <= 600:
        raise ValueError("TTL must be 1–1800 seconds; fixture delay 0–600 seconds")
    if args.adapter == "codex" and (not args.allow_model_calls or args.delay):
        raise ValueError("Codex requires --allow-model-calls and does not accept --delay")
    image = args.image or f"rtv-m1-{args.adapter}"
    image_id = _docker("image", "inspect", "--format", "{{.Id}}", image)
    return {
        "adapter": args.adapter,
        "ttl_seconds": args.ttl,
        "delay_seconds": args.delay,
        "model": (args.model or DEFAULT_MODEL) if args.adapter == "codex" else None,
        "image_id": image_id,
        "task_digest": tree_digest(task_path(args.adapter)),
        "stages": 2 if getattr(args, "staged", False) else 1,
        "max_recoveries": recoveries,
        "pause_after_checkpoint": getattr(args, "pause_after_checkpoint", False),
        "isolated_verifier": getattr(args, "pause_after_checkpoint", False),
    }


def run(args) -> int:
    from rtv.runtime.control import reconcile, summary
    from rtv.runtime.store import Store

    settings = Settings.load()
    store = Store(settings)
    cmd = args.runtime_command
    if cmd == "init":
        store.initialize()
        result = {"initialized": True, "root": str(settings.root), "queue": settings.queue}
    elif cmd == "prepare":
        subprocess.run(
            [
                "docker",
                "build",
                "-t",
                f"rtv-m1-{args.adapter}",
                str(task_path(args.adapter) / "environment"),
            ],
            check=True,
        )
        result = {"image": f"rtv-m1-{args.adapter}", "model_calls": 0}
    elif cmd == "worker":
        from rtv.runtime.service import serve

        if args.allow_model_calls:
            from rtv.codex_agent import validate_auth

            if args.auth_file is None:
                raise ValueError("Model-enabled worker requires --auth-file")
            os.environ["RTV_CODEX_AUTH_FILE"] = str(validate_auth(args.auth_file))
            os.environ["RTV_ALLOW_MODEL_CALLS"] = "1"
        else:
            os.environ.pop("RTV_ALLOW_MODEL_CALLS", None)
        if args.allow_api_calls:
            keyfile = args.api_key_file
            if keyfile is not None and (keyfile.is_symlink() or not keyfile.is_file()):
                raise ValueError("API key file must be a regular file")
            if keyfile is None and not os.environ.get("OPENAI_API_KEY"):
                raise ValueError(
                    "Set OPENAI_API_KEY or --api-key-file before enabling metered calls"
                )
            os.environ["RTV_ALLOW_API_CALLS"] = "1"
            if keyfile is not None:
                os.environ["RTV_API_KEY_FILE"] = str(keyfile.resolve())
        else:
            os.environ.pop("RTV_ALLOW_API_CALLS", None)
        asyncio.run(serve(settings))
        return 0
    elif cmd == "submit":
        from temporalio.client import Client

        from rtv.runtime.service import ensure_workflow

        row = store.submit(args.key, make_spec(args))

        async def dispatch():
            client = await Client.connect(settings.temporal)
            await ensure_workflow(client, settings, row["trial_id"])

        result = summary(row)
        try:
            asyncio.run(dispatch())
            result["dispatch"] = "acknowledged"
        except Exception:
            # The request is durable even if Temporal is temporarily unavailable.
            result["dispatch"] = "pending_reconciliation"
    elif cmd in {"handoff", "fork"}:
        from rtv.runtime.handoff import plan, target_spec
        from rtv.runtime.relay_store import RelayStore

        row = store.get(args.trial_id)
        if args.adapter == "mini" and not args.allow_api_calls:
            raise ValueError("Metered target requires --allow-api-calls")
        if (
            args.adapter == "codex" or (cmd == "fork" and row["spec"]["adapter"] == "codex")
        ) and not args.allow_model_calls:
            raise ValueError("Codex target requires --allow-model-calls")
        relays = RelayStore(settings)
        if cmd == "fork":
            branches = plan(
                row["spec"],
                adapter=args.adapter,
                model=args.model,
                portable=args.portable,
                ttl=args.ttl,
            )
            result = {"experiment_id": relays.create_experiment(row, args.key, branches, args.ttl)}
        else:
            target = target_spec(
                row["spec"], args.adapter, args.model, args.context, ttl=row["spec"]["ttl_seconds"]
            )
            result = relays.request(row, args.key, target, args.context)
    elif cmd in {"experiment", "experiments"}:
        from rtv.runtime.relay_store import RelayStore
        from rtv.runtime.views import experiment_view

        result = (
            experiment_view(settings, args.experiment_id)
            if cmd == "experiment"
            else RelayStore(settings).experiments()
        )
    elif cmd == "serve":
        from rtv.runtime.views import serve

        serve(settings, args.port)
        return 0
    elif cmd == "reconcile":
        result = [reconcile(settings, row["trial_id"], launch=False) for row in store.pending()]
    elif cmd == "events":
        result = store.events(args.trial_id)
    elif cmd == "ledger":
        result = store.ledger(args.trial_id)
    elif cmd == "history":
        result = store.executions(args.trial_id)
    elif cmd == "retire":
        store.retire(args.trial_id)
        result = {"trial_id": args.trial_id, "artifact_references_released": True}
    elif cmd == "recover":
        from rtv.runtime.control import recover

        result = summary(recover(settings, store.get(args.trial_id), args.key))
    elif cmd == "gc":
        from rtv.runtime.checkpoint import collect

        result = collect(settings, args.older_than)
    elif cmd in {"pin", "unpin"}:
        store.pin(args.checkpoint_id, args.owner, remove=cmd == "unpin")
        result = {"checkpoint_id": args.checkpoint_id, "owner": args.owner, "pinned": cmd == "pin"}
    elif cmd == "cancel":
        result = summary(store.cancel(args.trial_id))
    else:
        result = summary(store.get(args.trial_id))
    print(json.dumps(result, indent=2, default=str))
    return 0
