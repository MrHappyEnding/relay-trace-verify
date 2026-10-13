# RTV Documentation and acceptance index

The README presents current capabilities. These documents retain implementation details and versioned acceptance records. Test types are explicit: deterministic fixtures, real containers, fake-credential probes, and real models. Development observations remain separate from formal evaluation.

| Document | Contents |
| --- | --- |
| [Framework development](framework.md) | Package layout, core CLI, and extension interfaces |
| [M0: containers and adapters](m0.md) | Task format, container restore, and capability matrix |
| [Native Codex sessions](codex-m0.md) | Real session continuation and workspace restore |
| [M0: recovery boundaries](m0-boundaries.md) | Cancellation, rollback, and ATIF text import |
| [M1: durable execution](m1.md) | Submit, cancel, worker adoption, and cleanup |
| [M2: checkpoints](m2.md) | Conditional commits, execution ownership, and accounting |
| [M3: handoff and branches](m3.md) | Real agents, context policies, and the read-only viewer |
| [M4: local Kubernetes](m4.md) | Deployment, resource admission, and scripted fault acceptance |
| [Tasks and statistics](m4-benchmark.md) | Frozen four-task development and 20-task evaluation sets |
| [Repository integration](m4-repositories.md) | Task contracts, file modes, and isolated verifiers |
| [Repository cluster acceptance](m4-repository-cluster.md) | Task delivery, scripted handoff, and cross-node recovery |
| [Real-model pilot](m4-model-pilot.md) | Credential projection, natural-turn handoff, and historical validation |
| [Mid-task handoff protocol](m4-tool-prefix.md) | Tool allowances, frozen configuration, and boundary checks |
| [Development record: round one](m4-tool-prefix-results.md) | Four-task handoff and post hoc diagnostics |
| [Development record: round two](m4-early-prefix.md) | Environment correction and partial-patch handoff |
| [Development record: round three](m4-runtime-notice.md) | Runtime notification, command timeout, and complete grading |
| [Real-agent fault recovery](m4-model-recovery.md) | One executor loss, native session recovery, and resource cleanup |
| [Evaluation execution](m4-evaluation.md) | Frozen assignments, cumulative admission, interruption protection and archival |

Start with [demo and deployment](demo.md), [system design](design.md), or the [roadmap](roadmap.md). Sanitized evidence summaries live in [evidence/](evidence/); full trajectories, credentials, images, and large artifacts stay outside Git.
