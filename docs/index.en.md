# RTV Documentation and acceptance index

[![中文](assets/lang-zh.svg)](index.md) [![English](assets/lang-en.svg)](index.en.md)

The README presents current capabilities. These documents retain implementation details and versioned acceptance records. Test types are explicit: deterministic fixtures, real containers, fake-credential probes, and real models. Development observations remain separate from formal evaluation.

| Document | Contents |
| --- | --- |
| [Framework development](framework.en.md) | Package layout, core CLI, and extension interfaces |
| [M0: containers and adapters](m0.en.md) | Task format, container restore, and capability matrix |
| [Native Codex sessions](codex-m0.en.md) | Real session continuation and workspace restore |
| [M0: recovery boundaries](m0-boundaries.en.md) | Cancellation, rollback, and ATIF text import |
| [M1: durable execution](m1.en.md) | Submit, cancel, worker adoption, and cleanup |
| [M2: checkpoints](m2.en.md) | Conditional commits, execution ownership, and accounting |
| [M3: handoff and branches](m3.en.md) | Real agents, context policies, and the read-only viewer |
| [M4: local Kubernetes](m4.en.md) | Deployment, resource admission, and scripted fault acceptance |
| [Tasks and statistics](m4-benchmark.en.md) | Frozen four-task development and 20-task evaluation sets |
| [Repository integration](m4-repositories.en.md) | Task contracts, file modes, and isolated verifiers |
| [Repository cluster acceptance](m4-repository-cluster.en.md) | Task delivery, scripted handoff, and cross-node recovery |
| [Real-model pilot](m4-model-pilot.en.md) | Credential projection, natural-turn handoff, and historical validation |
| [Mid-task handoff protocol](m4-tool-prefix.en.md) | Tool allowances, frozen configuration, and boundary checks |
| [Development record: round one](m4-tool-prefix-results.en.md) | Four-task handoff and post hoc diagnostics |
| [Development record: round two](m4-early-prefix.en.md) | Environment correction and partial-patch handoff |
| [Development record: round three](m4-runtime-notice.en.md) | Runtime notification, command timeout, and complete grading |
| [Real-agent fault recovery](m4-model-recovery.en.md) | One executor loss, native session recovery, and resource cleanup |

Start with [demo and deployment](demo.en.md), [system design](design.en.md), or the [roadmap](roadmap.en.md). Sanitized evidence summaries live in [evidence/](evidence/); full trajectories, credentials, images, and large artifacts stay outside Git.
