# M0 Acceptance: ATIF, Interruption and Rollback

[![中文](assets/lang-zh.svg)](m0-boundaries.md) [![English](assets/lang-en.svg)](m0-boundaries.en.md)

2026-10-08: **M0 acceptance is complete within the first Codex adapter's stated
scope.** Native recovery and semantic handoff are now supplemented with same-model
ATIF import, cancellation during a real tool call, checkpoint rollback, image
lifecycle handling and explicit capabilities. M1–M4 are not implemented; this is
not automatic recovery after a worker crash.

## Reproduce

Use the Docker, Harbor and ChatGPT login setup in the [Codex guide](codex-m0.en.md).
These commands run one source stage, then an ATIF branch, an interrupted branch and
a rollback recovery branch. They consume existing subscription quota:

```bash
uv sync --python 3.13 --locked --group harbor --dev
uv run --no-sync rtv capabilities
uv run --no-sync rtv codex-prepare --output work/prepare-new

uv run --no-sync rtv codex-smoke --checkpoint-only \
  --auth-file "$HOME/.codex/auth.json" --allow-model-calls \
  --output work/checkpoint-new
uv run --no-sync rtv codex-boundaries \
  --source-run work/checkpoint-new \
  --auth-file "$HOME/.codex/auth.json" --allow-model-calls \
  --output work/boundaries-new
```

Always use new output directories. Without `--checkpoint-only`, `codex-smoke` still
runs its original three stages and its successful report is also a valid source.
The model defaults to `gpt-6-astra`, CLI is pinned to 0.160.1 and Harbor to 0.24.0.
Both commands must use the same model; API keys and custom endpoints are rejected.

Conversion-only auditing needs no model:

```bash
uv run --no-sync rtv codex-atif-audit \
  --native "work/checkpoint-new/bundle/native/rollout-<actual-filename>.jsonl" \
  --output work/atif-audit-new
```

Replace the rollout path with the actual filename. The CLI does not guess which
of several historical sessions should be considered the latest.

## What ATIF preserves

RTV reuses Harbor's native → ATIF → native conversion and `load()`. It changes the
receiver metadata's default `/app` to `/workspace`, sets the receiver CLI version
and assigns a new session ID while recording source lineage. **ATIF import is a
semantic branch, not lossless native recovery.**

All seven messages, three tool calls and three results in the source passed
normalized text/JSON comparisons. Message text blocks concatenate; tool output
blocks join with newlines, so byte identity is not claimed. The real imported
session recalled a marker absent from its new prompt/workspace and passed the task.

| Content | Verified scope |
| --- | --- |
| Text messages, tool arguments and text results | This fixture's content survives; calls/results must pair one-to-one |
| Workspace | Restored separately; ATIF does not contain filesystem state |
| Metadata and text block boundaries | Some are lost/normalized; recorded in the conversion audit |
| Native base instructions, world state and turn context | Not guaranteed to survive; receiver runtime config is rebuilt |
| Reasoning fields and native cumulative counters | Not reconstructed as native state; account for source costs separately |
| Images, segmented/nested traces, other agents/models | Unsupported inputs are rejected; cross-framework import unverified |

Harbor may estimate API dollar costs during conversion. RTV clears those estimates
for this subscription experiment. The ATIF receiver starts new native usage
counters; source usage remains in the source report and must not be treated as free.

## Real interruption protocol

1. Restore the committed workspace and native session; advance Execution epoch 1→2.
2. The real agent runs a controlled Python tool. It launches a child in a separate
   process session that continually modifies `uncommitted.txt`; the parent waits.
3. Verify both PIDs are alive and the file changes, then cancel the execution.
4. **Stop the whole sandbox and confirm container PID 0 before cancelling/draining
   the local transport task.** Cancelling a docker-exec client alone does not prove
   that remote tools have stopped.
5. Reject the incomplete turn and publish no new checkpoint. Discarded files and
   sessions remain diagnostics only; the source checkpoint directory hash is unchanged.
6. Create epoch 3 from the last committed checkpoint. Uncommitted files are absent,
   the original session ID/marker survive, and final task verification passes.

This is **rollback to the last completed stage after mid-tool cancellation**. It
does not restore process memory or continue the interrupted tool. The real run
covers explicit cancellation; fault-injection unit tests cover timeout, stop failure
and transport-error ordering. Hard worker kills, leases and stale-epoch fencing
remain M1/M2 work.

## Image and snapshot contract

Harbor `stop(delete=True)` uses `down --rmi local`, which removes generated source
images even when an extra tag was added. For this trusted fixture with no persistent
Docker volumes, RTV uses `stop(delete=False)` / Compose down to remove containers
and networks while retaining image artifacts. Reports include an explicit
`rtv-checkpoint-cache:<image-id>` reference. Recovery checks the exact image before
model execution and refuses to silently rebuild a different image.

Retained images are checkpoint dependencies, not container leaks; no global prune
is used. Retention is local only, with no registry publication, reference GC or
machine-loss guarantee. Older reports may point to deleted images and require a
new checkpoint. The successful local source-run metadata is also a required artifact.

`rtv capabilities` declares a snapshot of `/workspace`, one complete native rollout
and session metadata. Regular file bytes/permissions and empty directories survive;
symlinks, special files and traversal are rejected. Directory modes use process
defaults. UID/GID, ACLs, xattrs, processes and external service state are excluded.
Required generated files must live under `/workspace`; credentials and temporary
tool processes are outside the snapshot.

## Evidence and resources

- [Real experiment report](evidence/m0-boundaries-arm64-20261008.json): all 23
  assertions pass, both completed branches receive reward 1, cleanup errors are
  empty and the original image remains available after cleanup.
- Raw artifacts: `work/codex-checkpoint-20261008-2/` and
  `work/codex-boundaries-20261008-3/`. Sensitive raw traces remain in ignored `work/`.
- The image-retention issue required one extra source turn. Two environment failures
  issued no model calls. Development overhead is included in the committed report.
- The interrupted turn's final usage is `null`/UNKNOWN. Observed cumulative counters
  are only a lower bound and include the imported prefix; do not add them directly
  to totals. Completed-turn subtotals and uncertainty remain separate.
- Existing ChatGPT subscription quota was used, with no additional paid API enabled
  or invented dollar price. Wall-clock termination includes stop/cleanup grace
  periods and is not strict token/dollar admission.

Next is **M1 durable single-agent execution**: stable operation IDs, Temporal
orchestration, query/cancel behavior, then sandbox reconciliation after worker
restart. The second agent remains planned for M3.

References: [Harbor trajectory loading](https://docs.harborframework.com/jobs/loading-trajectories)
and [Codex non-interactive execution](https://learn.chatgpt.com/docs/non-interactive-mode).
Conversion/native resume are upstream capabilities; RTV supplies workspace,
environment, identity, termination ordering and validation boundaries.
