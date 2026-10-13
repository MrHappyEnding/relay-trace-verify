# M4: real-agent executor recovery

This acceptance run checks whether real Codex can recover a native session and code from a committed
checkpoint after executor loss. It is a separate reliability workload, excluded from paired benchmark
denominators.

## Execution contract

1. Source Codex completes a bounded turn in a pinned repository, quiesces its sandbox, and commits a checkpoint without reading the hidden verifier.
2. Create a native continuation branch and observe its running `codex exec resume` process.
3. Delete that branch's executor using its Kubernetes Job UID.
4. Allow at most one recovery, creating a new Job on another execution node and restoring the native session and files.
5. Grade with an isolated verifier, export execution history and accounting, and clean up Jobs, Pods, and credentials.

At most three Codex subscription stages are admitted: source, interrupted continuation, and recovery.
There are zero additional metered API requests. Interrupted subscription usage remains UNKNOWN;
the operation is retained in the ledger.

The `codex-recovery-v1` credential profile covers two preassigned trials. The source permits only its
first execution; the target permits its first execution plus one recovery from its inherited source
checkpoint. Ordinary paired runs continue to prohibit automatic model replay. The profile provides
no mini API credential, additional task grants, or unbounded retries.

## Prepare and execute

Follow [M4 deployment](m4.md) and [repository delivery](m4-repository-cluster.md).
Use a pinned runner image containing the current source and an uncredentialed single-Job repository
configuration. The variables below point to that configuration, its task contract, and a private
Codex credential file. Never put credential contents into commands.

First run fake-credential checks on the real cluster, without calling models:

```bash
.venv/bin/python scripts/m4_credentials_acceptance.py \
  --config "$RTV_RECOVERY_CONFIG" --contract "$RTV_RECOVERY_CONTRACT" \
  --output work/m4-data/recovery-credential-check --recovery
```

Freeze task identities, configuration, source hashes, and the recovery allowance:

```bash
.venv/bin/python scripts/m4_model_recovery.py prepare \
  --config "$RTV_RECOVERY_CONFIG" --contract "$RTV_RECOVERY_CONTRACT" \
  --root work/m4-data/recovery-demo \
  --output work/m4-control/recovery-demo-plan.json
```

Execute within the authorized model-call scope:

```bash
.venv/bin/python scripts/m4_model_recovery.py execute \
  --prepared work/m4-control/recovery-demo-plan.json \
  --auth-file "$RTV_CODEX_AUTH_FILE" --allow-model-calls
```

The run directory contains `prepared.json`, `fault.json`, `report.json`, and `runtime-report/`.
A new run requires new paths and a new plan; previous records remain unchanged.

## Acceptance criteria

Check workspace digests, native session ID, checkpoint identity, attempt identity, absolute deadline,
execution count, stale-execution publication rights, and cleanup. Record verifier reward separately;
relative strategy performance does not determine recovery acceptance.

Docker Desktop's macOS mount view can expose Git objects with mode `0644` where Linux sees `0444`.
Compare container restore events with the committed Linux workspace digest; use host digests only
for before/after immutability within the host view. Checkpoint restoration still validates manifest
content, file types, and permissions.

Local k3d execution nodes share one physical host. This run injects executor Job loss. It does not
test physical-host power failure, rollback of arbitrary external effects, or process-memory recovery.

## Acceptance record

On 2026-10-13, a targeted `sympy__sympy-22914` run recovered on the other execution node,
preserved its native session, and passed the isolated verifier (`reward=1`). The checkpoint contains
changes to `sympy/printing/pycode.py` and its tests. Time from Job deletion initiation to restored
workspace was **82.25 seconds** for this one observation.

The [evidence](evidence/m4-model-recovery-arm64-20261013.json) contains 18 passing offline acceptance
checks and 17 passing real-cluster fake-credential checks. The first inspector revision stopped before
fault injection because it compared different filesystem views. The next run completed the actual
recovery and grading; its final inspector check detected a Git-index refresh caused by its own
`git diff`. The committed checkpoint remained intact. `m4_recovery_audit.py` revalidated all committed
files, restored modes, execution records, and the original grade without calling a model or verifier
again. Original harness statuses and checks are retained in the evidence. The inspector now uses
`git --no-optional-locks`, covered by a regression test.

The two runs used five Codex subscription stages in total, zero metered API requests, and $0 additional
API charges. Subscription cost remains unknown. Task Jobs, Pods, and credentials were cleaned up.
The existing fixed 20-task formal evaluation remains a separate pending milestone item.

To independently audit saved artifacts without a database or cluster connection:

```bash
.venv/bin/python scripts/m4_recovery_audit.py \
  --root work/m4-data/recovery-demo --output work/recovery-audit.json
```

