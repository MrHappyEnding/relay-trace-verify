# RTV demo and deployment

[![中文](assets/lang-zh.svg)](demo.md) [![English](assets/lang-en.svg)](demo.en.md)

Explore RTV through the following three levels. Use a new output directory for every run;
historical runs remain separately inspectable and reproducible.

## 1. Core handoff demo

Requires Python 3.11+ and uv. No Docker, database, or model credentials are needed.

```bash
uv sync --locked --dev
uv run rtv demo --output work/demo
```

The flow is: A edits a file → commit a checkpoint → B restores into an independent directory
→ finish the change → run the verifier. Inspect `report.json` and `events.jsonl` in the output
directory, alongside its workspaces and checkpoints. This uses deterministic fixtures to demonstrate
the protocol; it is not a real-model result.

## 2. Durable execution and the viewer

Follow [M1 service deployment](m1.en.md) to start Docker, PostgreSQL, and Temporal. Then follow
the [M3 model-free example](m3.en.md) to start a worker, submit a staged task, and create branches.

```bash
uv run --no-sync rtv runtime serve --port 8765
```

Open <http://127.0.0.1:8765> and select 中文 or English. The viewer shows the shared checkpoint,
branches, execution events, usage, and cleanup. Use the same runtime root and database settings as
the submitted tasks. The viewer is read-only and listens on loopback.

## 3. Local Kubernetes and real agents

The [M4 deployment guide](m4.en.md) covers the pinned local k3d cluster, image imports, and service
startup. Run deterministic fault acceptance first to check execution nodes, PostgreSQL, Temporal,
and cleanup. Real repositories also require [offline verifier preflight](m4-benchmark.en.md) and
[task delivery](m4-repository-cluster.en.md).

Real models have two separate entry points:

| Scenario | Entry point | Model allowance |
| --- | --- | --- |
| Codex / mini-swe-agent handoff | [Model pilot](m4-model-pilot.en.md) | Codex subscription stages and bounded API branches |
| Recovery after an active executor is lost | [Real-model recovery](m4-model-recovery.en.md) | At most three Codex stages and one recovery; no metered API calls |

Preparation only freezes configuration. Execution requires explicit model opt-in. Credentials are
projected from private files into the owned run, never written into configuration or Git. Subscription
usage is reported separately; zero API charges do not imply free inference.

## What to inspect

1. Source workspace and manifest remain unchanged after checkpoint publication.
2. Handoff or recovery receives the same files and the specified context.
3. Stale executions cannot publish; recovery preserves the original deadline and accounting.
4. Independent verification completes, with task outcome recorded separately from runtime state.
5. Jobs, Pods, sandboxes, and run-scoped credentials are cleaned up afterwards.

A walkthrough can show the checkpoint, execution history, recovery record, and final report in turn.
See the [documentation index](index.en.md) and [roadmap](roadmap.en.md) for development records and
formal evaluation progress.
