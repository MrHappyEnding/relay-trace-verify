# ADR 0001: Boundaries Between RTV and Existing Frameworks

- Date: 2026-10-07
- Status: Accepted after M0; ADR 0002 refines the local M1 implementation

## Context

RTV addresses state consistency for long-running coding agents during interruptions, handoffs, and branching experiments. Harbor already provides trajectory formats, some cross-agent trajectory loading, persistent environments across multi-step tasks, and hosted execution. Temporal already provides durable orchestration. Merely wrapping a run API or chaining two agents would not adequately express the project's technical contribution.

## Proposal

1. Reuse Harbor tasks, agent/sandbox adapters, verifiers, and ATIF. M0 will establish the specific integration interfaces.
2. Use Temporal to orchestrate control flow. Activity inputs and outputs should contain small references; store large raw artifacts in object storage.
3. Implement checkpoint commits, ownership/epochs, recovery and handoff protocols, budget semantics, and branching experiments in RTV.
4. Maintain product objects and conditional updates in PostgreSQL. Use stable operation IDs and reconciliation to handle lost acknowledgements across systems; do not assume exactly-once external side effects.
5. Assign creation, takeover, and cleanup of each resource to a single layer. Harbor's job scheduler and the upper-level scheduler must not independently retry the same execution.
6. Initially guarantee application-level recovery at stage boundaries only. Tool-boundary recovery requires explicit adapter support.

## Alternatives and tradeoffs

| Option | Advantage | Cost or conclusion |
| --- | --- | --- |
| Wrap only the Harbor CLI | Fastest route to an end-to-end run | Precise interruption and state-boundary control is difficult; useful as a probe, not an assumed final design |
| Build evaluation and agent frameworks from scratch | Full control | Duplicates existing capabilities and imposes excessive maintenance |
| Build a durable workflow engine | Deep general-purpose scheduling work | Moves away from the core problem; reuse Temporal initially |
| Snapshot arbitrary processes directly | Preserves more system state | Portability and compatibility exceed the initial recovery contract |

## Consequences

Dependency versions and agent capabilities require contract tests. Record information loss from native/portable session conversion explicitly. Validate project outcomes through fault injection and fair experiments separately; attribute existing framework capabilities to their respective dependencies.

## Acceptance conditions

M0 must demonstrate control of sandbox lifecycle, stage-boundary quiescence, session export/import, and verifier invocation at the selected integration layer. If it cannot, add an ADR to revise the integration approach while retaining this decision's history.

## Sources

- https://docs.harborframework.com/jobs/loading-trajectories
- https://docs.harborframework.com/tasks/multi-step
- https://docs.harborframework.com/hosted-harbor
- https://docs.temporal.io/activity-definition
