"""Explicit opt-in cluster controls; core CLI remains dependency-free."""

import argparse
import asyncio
import json
import os
from pathlib import Path


def add_parser(commands):
    parser = commands.add_parser("cluster", help="M4 model-free Kubernetes execution")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    sub = parser.add_subparsers(dest="cluster_command", required=True)
    for name in ("init", "worker", "pool"):
        sub.add_parser(name)
    submit = sub.add_parser("submit")
    submit.add_argument("--key", required=True)
    submit.add_argument("--ttl", type=int, default=600)
    submit.add_argument("--delay", type=int, default=0)
    submit.add_argument("--staged", action="store_true")
    submit.add_argument("--pause-after-checkpoint", action="store_true")
    submit.add_argument("--max-recoveries", type=int, default=0)
    for name in ("status", "cancel", "events", "history", "ledger"):
        sub.add_parser(name).add_argument("trial_id")
    report = sub.add_parser("report")
    report.add_argument("--output", type=Path, required=True)


def configure(path, root):
    from rtv.cluster.config import ClusterConfig
    from rtv.runtime.config import Settings

    os.environ["RTV_CLUSTER_CONFIG"] = str(path.resolve())
    config = ClusterConfig.load()
    os.environ.update(
        RTV_SCHEMA="rtv4",
        RTV_RUNTIME_ROOT=str(root.resolve()),
        RTV_DATABASE_URL=config.control_database_url,
    )
    settings = Settings.load()
    config.validate_root(settings)
    settings.root.mkdir(parents=True, exist_ok=True)
    return settings, config


def fixture_spec(**overrides):
    from rtv.runtime.cli import make_spec

    values = dict(
        adapter="fixture",
        ttl=600,
        delay=0,
        image=None,
        model=None,
        allow_model_calls=False,
        staged=False,
        max_recoveries=0,
        pause_after_checkpoint=False,
    )
    values.update(overrides)
    spec = make_spec(argparse.Namespace(**values))
    spec["isolated_verifier"] = True
    return spec


def run(args):
    from rtv.cluster.store import ClusterStore
    from rtv.runtime.control import summary

    settings, config = configure(args.config, args.root)
    store = ClusterStore(settings)
    cmd = args.cluster_command
    if cmd == "init":
        store.initialize_pool(config)
        result = {"root": str(settings.root), "schema": settings.schema, "queue": settings.queue}
    elif cmd == "worker":
        from rtv.runtime.service import serve

        store.initialize_pool(config)
        asyncio.run(serve(settings))
        return 0
    elif cmd == "submit":
        row = store.submit(
            args.key,
            fixture_spec(
                **{
                    k: getattr(args, k)
                    for k in ("ttl", "delay", "staged", "pause_after_checkpoint", "max_recoveries")
                }
            ),
        )
        result = summary(row)
    elif cmd == "pool":
        result = store.pool_status()
    elif cmd == "report":
        from rtv.cluster.report import export_report

        result = export_report(settings, config, args.output)
    elif cmd in {"events", "ledger", "history"}:
        method = store.executions if cmd == "history" else getattr(store, cmd)
        result = method(args.trial_id)
    elif cmd == "cancel":
        result = summary(store.cancel(args.trial_id))
    else:
        result = summary(store.get(args.trial_id))
    print(json.dumps(result, indent=2, default=str))
    return 0
