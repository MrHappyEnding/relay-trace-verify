# ADR 0002: M1 local supervisor and durable control plane

[![中文](../assets/lang-zh.svg)](0002-local-supervisor.md) [![English](../assets/lang-en.svg)](0002-local-supervisor.en.md)

- Date: 2026-10-08
- Status: Accepted for trusted tasks on one host

## Problem

Activities may retry after their side effects occur but acknowledgement is lost.
Putting a complete Harbor/Codex turn in an Activity could duplicate workspace changes
and model calls. M0's async task lifetime also does not support observing a sandbox
after the worker receives SIGKILL.

## Decision

Temporal runs short control Activities. PostgreSQL holds product identities and cancel
intent; periodic reconciliation closes the metadata-commit/Workflow-start gap. Each
Execution gets a detached local supervisor, stable Compose project and artifact directory.
A local `flock` and pre-side-effect start intent prevent reentry. Lost supervisors are
not replayed. A local result is written, external resources are cleaned, then a database
transaction publishes the terminal outcome and event. Failed cleanup remains pending.

Temporal development persistence uses a SQLite file; product metadata remains PostgreSQL
as in ADR 0001. Raw artifacts currently use ignored local directories; object storage
remains M2/M4. M0 session recovery/handoff probes stay independent rather than becoming
an automatic recovery policy in this milestone.

## Tradeoffs

Reuse Harbor task/agent/verifier and most public environment interfaces. Three internal
transport hooks pinned to 0.24.0 add default timeouts and cancellation draining, avoiding
late operations after merely canceling a client. This adds upgrade maintenance, covered
by unit tests and real cancellation experiments.

File locks coordinate through one host's kernel; they are not distributed leases or
fencing. Disk/host loss, a supervisor SIGKILL during sandbox creation causing late
resources, and multi-host takeover are outside this guarantee. Preserve evidence and
extend the protocol with conditional commits, epochs and resource collection later.
Old-worker return and strict treatment of uncertain side effects belong to M2. M1 does
not promise exactly-once model calls or external tools.

## Validation

[M1 guide and evidence](../m1.en.md) cover real Activity acknowledgement loss, worker
replacement, cancellation, TTL, supervisor loss and one real Codex turn across worker
SIGKILL. Temporal orchestration, PostgreSQL transactions and POSIX locking are reused;
RTV implements operation identities, reconciliation and execution boundaries.
