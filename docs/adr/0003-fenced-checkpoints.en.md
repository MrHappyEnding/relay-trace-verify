# ADR 0003: M2 conditional checkpoints and execution ownership

[![中文](../assets/lang-zh.svg)](0003-fenced-checkpoints.md) [![English](../assets/lang-en.svg)](0003-fenced-checkpoints.en.md)

- Date: 2026-10-09
- Status: Accepted for single-host runtime v2; extends ADR 0001 / 0002

## Problem

M1 can observe a surviving supervisor but cannot recover a lost execution. Interrupted
uploads can leave incomplete artifacts; a returning old process can overwrite a newer
result; cumulative native usage can double-count a prefix. File existence, process
continuation, semantic handoff and product success need distinct authoritative boundaries.

## Decision

1. Keep Trial/Attempt identity and create a new Execution and epoch for each stage advance
   or recovery. Each Execution owns a directory and Compose project. Persist the physical
   call operation ID before invocation and reject model-call readmission.
2. Store the current pointer and lease in PostgreSQL, using its clock. Lock the stable
   Trial row first, then read its current Execution in a fresh statement. Only one concurrent
   request can advance an observed epoch. State, event, checkpoint and result publication
   check the expected execution/epoch/attempt.
3. Use PREPARING → COMMITTED: stop the sandbox, write files and manifest with fsync, validate
   an independent restore, then conditionally commit the DB pointer and event. There is no
   distributed PostgreSQL/filesystem transaction, and rename alone is not product commit.
   The v1 learning format remains; the runtime uses v2.
4. Never overwrite files for an existing checkpoint ID. Reconcile lost upload/DB replies
   against the same ID; reject publication after the source execution is superseded. If
   the boundary committed but its result acknowledgement was lost, the controller can
   finalize from that record. Restore only COMMITTED inputs, rechecking content and compatibility.
5. Accumulate PENDING/KNOWN/UNKNOWN operations per physical stage call. A late usage
   observation may settle its original row; it grants no authority over current results.
   Recovery retains history, never treats unknown cost as zero, and does not invent a
   subscription dollar bill. Recovery allowance is separate from TTL; TTL never resets.
6. Explicit recovery keys prevent duplicate epoch advances. Automatic recovery is limited
   to supervisor loss or lease expiry with an existing commit and explicit allowance.
   Container/adapter errors allow manual recovery. Terminal product trials are not replayed;
   legitimate recovery first changes product state, then allows a new Temporal control run.
7. GC locks metadata, checks implicit/explicit references and writes deletion tombstones
   before removing files. New pins, commits and restores reject deleted artifacts. Retry
   sweeps handle deletion failure or late uploads. Releasing a terminal Trial's implicit
   references requires explicit retirement; tombstones remain, and images/raw logs are
   managed separately.

## Tradeoffs

Use local storage on one host while recovery semantics settle. Fsync and hashes clarify
commit boundaries, but power loss, recovery from disk corruption, cross-host storage and
distributed resource fencing remain unverified. DB leases fence authoritative writes;
they cannot give arbitrary external APIs exactly-once behavior. Independent directories
isolate old workspace writes; reconciliation repeatedly cleans execution-specific sandboxes.

Checkpoints require the entire sandbox to be quiescent and restore completed stages.
In-flight tool processes and file changes are discarded; RTV does not intercept every
internal Codex tool. Conservative snapshots and a cumulative ledger precede any measured
case for incremental snapshots, finer tool protocols or concurrent scan optimization.

Runtime v2 has a separate `rtv2` schema, default root and queue so active M1 processes
cannot interpret the new protocol. History is not automatically migrated; commit `0b46ea4`
reproduces M1. Actual experiment branches, the second adapter and multi-host lifecycle
protocols remain M3/M4.

## Validation and sources

Acceptance separates real-service/container fixtures, synthetic protocol faults on real
PostgreSQL/filesystem, and two real Codex subscription calls; see [M2](../m2.en.md).
Testing exposed and fixed a locking join that missed the current row during concurrent
pointer changes, and failure to restart a closed Workflow after manual product recovery.

- [PostgreSQL explicit locking](https://www.postgresql.org/docs/17/explicit-locking.html): reuse row locks and transactions to serialize product state changes.
- [PostgreSQL constraints](https://www.postgresql.org/docs/17/ddl-constraints.html): reuse uniqueness constraints for operation identity and epochs.
- [Temporal Activities](https://docs.temporal.io/activity-definition): reuse durable control retries; physical model calls are outside replayable Activities.
- [Harbor trajectory loading](https://docs.harborframework.com/jobs/loading-trajectories): reuse adapter session loading; RTV supplies workspace snapshots, ownership and usage semantics.

This applies established transaction, lease, idempotency and checkpoint mechanisms for
learning. RTV's implementation contribution is the cross-layer contract, protocol integration
and reproducible fault validation; these general mechanisms are not claimed as new algorithms.
