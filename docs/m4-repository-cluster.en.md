# M4: Kubernetes repository execution and observation linkage

[![中文](assets/lang-zh.svg)](m4-repository-cluster.md) [![English](assets/lang-en.svg)](m4-repository-cluster.en.md)

**Status: 28 real-cluster scripted-control checks pass; M4 formal evaluation remains incomplete.**
[Acceptance evidence](evidence/m4-repository-cluster-arm64-20261012.json) includes raw artifact hashes and failure history.

This stage runs `sympy__sympy-22914` on local k3d with real Jobs, Harbor containers, PostgreSQL,
Temporal and the mini-swe-agent loop. Actions are scripted and use the upstream reference patch.
There are no model calls or agent-effectiveness conclusions. The other three pilot tasks and the
evaluation set have not run.

## Delivery and resources

`cluster prepare-repository` creates a separate task package on the shared volume. Original task
definitions remain intact; the delivered path gets a new contract digest. Task contents, immutable
agent/verifier image IDs and the AMD64 platform remain bound. Each Job checks the Docker archive
SHA256 before loading and checks repository image identities afterwards. Contracts contain no API
keys. Default pools still reject real models; the subsequent opt-in path is described in
[model credentials and pilot](m4-model-pilot.en.md).

| Setting | Repository pool | Existing small-fixture pool |
| --- | --- | --- |
| Concurrent Jobs | 1 | 2 |
| CPU / memory per Job | 2 CPU / 5120 MiB | 1 CPU / 1536 MiB |
| Docker engine temporary directory | 16 GiB | 4 GiB |
| Pod ephemeral-storage request / limit | 17 GiB | 5 GiB |
| Image-load timeout | 300 seconds | 90 seconds |
| Container startup timeout | 180 seconds | 60 seconds |

The inner task declares 1 CPU, 4 GiB memory and 10 GiB storage; the outer envelope includes daemon
and executor overhead. The local namespace memory quota is now 6144 MiB; its CPU quota remains
2500m, including PostgreSQL. Use a new runtime root when the frozen runner image or pool configuration
changes. Kubelet monitors ephemeral storage; this is not a synchronous filesystem hard quota.

The outer runner uses native ARM64; the inner repository container declares `linux/amd64`, using this
Docker Desktop installation's cross-architecture support. Arbitrary ARM Kubernetes clusters are not
therefore supported. No GPU workload starts. The design extends [ADR 0005](adr/0005-kubernetes-executions.en.md),
reusing Harbor environments and the upstream SWE-bench verifier. RTV supplies identities, delivery
contracts, recovery boundaries and observation linkage.

## Assignments to observations

`benchmark.execution.prepare_pair` writes `run.json` before submitting any Trial. It binds the
original protocol SHA, task/repeat, branch order, Trial identities, context policies and execution
specs. This page covers pilot oracle controls; the subsequent launcher also supports an explicit
real-model mode. Prefix and continuation stages get 300 seconds each;
fresh gets 600 seconds. The absolute Trial deadline is 1800 seconds including preparation and
verification. Experiment branches have no automatic recovery. A separate fault Trial cannot replace
an assigned observation.

`collect-run` derives outcomes from database snapshots and validates execution specs, source
checkpoints, prefix operation IDs and actual handoff policies. Exports include raw SHA256 references,
queue/elapsed times, ledgers, JSONL, CSV and bilingual reports. A retained committed sealed checkpoint
keeps historical lineage verifiable even after the source Trial is cleaned up or expires.

- Reward zero without completed verification is an infrastructure error, not a graded failure.
- Missing branches remain in the original denominator. This run covers 4/16 pilot observations;
  the other 12 remain missing.
- UNKNOWN costs remain null alongside known lower bounds. Subscription inference is not priced as free.
- Real model operations cannot enter an oracle report. Incomplete observations suppress confidence intervals.
- The acceptance script stops and cleans up on a failed control. It is not a complete formal experiment scheduler.

## Reproduction and export

Complete [local repository integration](m4-repositories.en.md) and [local cluster setup](m4.en.md).
Keep the PostgreSQL forward and Temporal service running. Choose fresh output directories and retain
both failed and successful artifacts under `work/`.

```bash
.venv/bin/python scripts/m4_local_setup.py --build

.venv/bin/python -m rtv cluster \
  --config work/m4-control/config.json --root work/m4-data/preparation \
  prepare-repository --contract work/m4-benchmark/runtime-contract.json \
  --output work/m4-data/repository-package-new

.venv/bin/python scripts/m4_repository_cluster_acceptance.py \
  --config work/m4-data/repository-package-new/config.json \
  --contract work/m4-data/repository-package-new/contract.json \
  --output work/m4-data/repository-cluster-new
```

Acceptance executes the prefix and four branches in their frozen order, then separately deletes a
running Job. Cleanup uses this pool's exact Job UIDs and releases reservations only after Jobs and
Pods disappear. Recovery creates a new epoch from the inherited checkpoint on another node, preserving
the deadline and UNKNOWN invocation.

Re-export database observations without launching tasks or models:

```bash
RTV_SCHEMA=rtv4 RTV_RUNTIME_ROOT="$PWD/work/m4-data/repository-cluster-new" \
RTV_DATABASE_URL=postgresql://rtv:rtv-local-cluster@127.0.0.1:55434/rtv \
  .venv/bin/python -m rtv benchmark collect-run \
  --run work/m4-data/repository-cluster-new/run.json \
  --output work/m4-data/repository-cluster-new/observations-new
```

## Acceptance boundaries and observed differences

In this Docker Desktop shared mount, four read-only Git files had Linux mode `0444` but macOS host
mode `0644`. Acceptance therefore compares digests within each view: host source versus host restored
directories, and container `workspace.restored` events versus the original execution digest. The
manifest retains original permissions. Runtime restores and independent verifiers still check paths,
contents and permissions; Git metadata is not excluded. The initial failed cross-view comparison is
retained in the evidence history.

A later attempt completed repair and tests but failed while deleting a shared validation scratch
directory containing newly appearing `.DS_Store` files. Checkpoint, recovery and handoff validation
now use process-private temporary directories; committed artifacts remain on shared storage. Another
attempt hit the legacy 60-second container startup limit while local regression was also running.
Repository startup now has a separate 180-second limit; tool timeouts and the Trial deadline remain
bounded independently. Heavy acceptance runs should be serialized on this development host.

Both Kubernetes nodes share one physical Docker Desktop host and local storage. Job deletion and
node migration do not prove host-loss availability. Outer Pods remain privileged, for trusted local
development experiments only. The subsequent [credential path](m4-model-pilot.en.md) passes
fake-credential checks and native continuation/cross-agent workflow acceptance on one repository.
Formal effectiveness and cost comparisons remain pending. New paid runs require pricing and approval.
This stage uses zero API calls, $0 cloud spend and no GPUs.
