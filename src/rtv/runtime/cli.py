"""Opt-in M2 CLI. Config and auth stay outside durable Workflow payloads."""

import asyncio
import json
import os
import subprocess
from pathlib import Path

from rtv.codex_agent import DEFAULT_MODEL
from rtv.harbor_probe import _docker, tree_digest
from rtv.runtime.config import Settings, task_path


def add_parser(commands):
    parser = commands.add_parser("runtime", help="Local Temporal/PostgreSQL M2 runtime")
    sub = parser.add_subparsers(dest="runtime_command", required=True)
    sub.add_parser("init", help="Initialize PostgreSQL metadata tables")
    prepare = sub.add_parser("prepare", help="Build a local fixture image without model calls")
    prepare.add_argument("--adapter", choices=["fixture", "codex"], default="fixture")
    worker = sub.add_parser("worker", help="Run the local control worker and reconciler")
    worker.add_argument("--allow-model-calls", action="store_true")
    worker.add_argument("--auth-file", type=Path)
    submit = sub.add_parser("submit", help="Idempotently submit one trusted coding fixture")
    submit.add_argument("--key", required=True)
    submit.add_argument("--adapter", choices=["fixture", "codex"], default="fixture")
    submit.add_argument("--image", help="Prepared local image tag or immutable ID")
    submit.add_argument("--ttl", type=int, default=300)
    submit.add_argument("--delay", type=int, default=0, help="Fixture-only fault-test delay")
    submit.add_argument("--model", default=DEFAULT_MODEL)
    submit.add_argument("--allow-model-calls", action="store_true")
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


def make_spec(args) -> dict:
    recoveries = getattr(args, "max_recoveries", 0)
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
        "model": args.model if args.adapter == "codex" else None,
        "image_id": image_id,
        "task_digest": tree_digest(task_path(args.adapter)),
        "stages": 2 if getattr(args, "staged", False) else 1,
        "max_recoveries": recoveries,
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
