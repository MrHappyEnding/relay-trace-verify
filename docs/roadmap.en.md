# RTV Roadmap and Acceptance Criteria

[![中文](assets/lang-zh.svg)](roadmap.md) [![English](assets/lang-en.svg)](roadmap.en.md)

Status: F0-M3 accepted locally; M4 local foundation and task/statistics protocol implemented, milestone still in progress (2026-10-13)

The initial framework completes F0 below. M0 is accepted within the first Codex adapter’s stated scope; M1/M2 are implemented on one host; M3 protocol, scripted-model and real two-agent development-task acceptance are complete; M4 has a local model-free Kubernetes foundation; formal experiments remain outstanding. Local fixtures and mocked protocol tests do not replace real-container or real-agent compatibility tests. Reproduction and original implementation both remain in scope; see the [learning plan](learning-plan.en.md).

## F0: Framework skeleton

- [x] Python package, `rtv` CLI, and uv dependency lockfile.
- [x] Trial state transitions, Attempt/Execution identities, and adapter contracts.
- [x] Local file checkpoints, hash validation, and restoration into independent directories.
- [x] JSONL events, a two-stage fixture relay, and a final verification report.
- [x] Tests for states, isolation, corruption detection, and commit failure windows.

See the [framework development guide](framework.en.md) for the current implementation boundary.

## M0: Compatibility and protocol prototype

- [x] Pin Harbor 0.24.0 and the base-image digest; validate the minimal fixture with the real Task parser.
- [x] Implement read-only preflight, a file recovery prototype, mocked failure tests, and an opt-in real-container test.
- [x] Pin the first agent: Codex CLI 0.160.1, `gpt-6-astra`, and local Docker versions; pin the second agent at M3.
- [x] Run a scripted fixture through real container file recovery; validate Harbor Task/sandbox/verifier integration.
- [x] Run the first real-agent task; reuse Harbor Codex while RTV controls stages and checkpoints.
- [x] Verify workspace export at stage completion, restoration into a new sandbox and pinned native-session import.
- [x] Verify pinned same-model Codex text ATIF import and conversion losses; cross-framework validation remains M3 work.
- [x] Cancel a real running tool and its child, confirm sandbox stop and rollback; publish scope with `rtv capabilities`.
- [x] Define workspace/session scope, regular-file modes, link rejection and generated-file rules; retain checkpoint images.
- [x] Current Codex supports soft control only; interrupted usage is UNKNOWN and strict token/dollar admission is unsupported.

**Acceptance evidence**: a recovery example between two isolated sandboxes, file-digest comparisons, compatibility records, and explicit rejection of unsupported capabilities. M0 does not require a complex web interface or Kubernetes.

See the [M0 guide](m0.en.md) for commands, protocol, and evidence. Scripted container recovery and the first real Codex native recovery/fresh-session handoff passed; see [Codex evidence](codex-m0.en.md). Follow-up ATIF and real cancellation/rollback evidence is in [M0 acceptance](m0-boundaries.en.md). Process-memory recovery and strict budgets remain unsupported; see [M1](m1.en.md) for worker takeover.

## M1: Durable single-agent execution

- [x] Trial/Attempt/Execution objects, state transitions, and stable operation IDs.
- [x] Temporal Workflows and control Activities whose results can be queried and safely retried.
- [x] Submission, status queries, cancellation, final verification, and artifact persistence.
- [x] Link control events, execution events, costs, and results from day one.
- [x] Reconciliation and sandbox takeover after worker restart, TTLs, and cleanup retries.

**Acceptance evidence**: duplicate submissions do not create duplicate valid executions; cancellation converges; existing sandboxes can be reconciled after restart; and tests leave no task resources behind.

See [M1 acceptance](m1.en.md) for implementation, 22 real-service checks and 7 real-Codex checks. Scope: takeover of a surviving supervisor on one host, without automatic replay of lost executions.

## M2: Checkpoints and reliable recovery

- [x] Manifest schema, content validation, quiescence barrier, and immutable artifact storage.
- [x] Conditional checkpoint commits, epoch fencing, and recovery into independent workspaces.
- [x] Handling of unresolved tool operations and UNKNOWN costs.
- [x] Fault injection at commit windows, during disconnection, and when old workers return.
- [x] Collection of unreferenced artifacts and protection of branch references.

**Acceptance evidence**: incomplete artifacts cannot be restored; committed files match after recovery; old epochs cannot publish; cumulative cost does not reset; and lost-work windows are recorded.

Accepted within the local scope: [M2 guide](m2.en.md), 40 runtime/protocol checks and 10 real-Codex checks. The ledger covers stage calls; per-tool external effects remain outside the guarantee. Branch reference protection is a pin/GC primitive; actual branches remain M3. Artifacts are local, without object-store replication or verified power-loss durability.

## M3: Two-agent handoff and experiment branching

- [x] Idempotent Handoff, target preparation, and transfer of execution control.
- [x] Task-only, summary, and portable-trajectory context strategies.
- [x] Isolated branches from the same checkpoint.
- [x] Paired experiments, prefix-cost attribution, and independent verification.
- [x] Minimal interface for state, checkpoint lineage, handoff trajectories, and results.

**Acceptance evidence**: A-continue, B-workspace, and B-summary can be rerun; branches cannot contaminate one another's files; source state, costs, and final verification are fully traceable.

**Current evidence**: the [M3 guide](m3.en.md) records 48 zero-cost protocol checks and 14 real-model checks. Native Codex continuation and all three mini-swe-agent handoffs complete one fixed task, with isolated verification, API usage, prefix attribution and cleanup checked. Three failed attempts and the passing fourth attempt remain documented. The local functional scope is accepted; benchmark performance is not established.

## M4: Multi-worker validation and complete report

- [x] Local Kubernetes backend; distinct owners for Job/Pod and inner sandbox lifecycles.
- [x] Bounded concurrency, admission, node/worker recovery and cleanup in the local multi-node scope.
- [x] Freeze tasks, execution caps, exclusions and statistics; revalidate and approve API reservations before paid execution.
- [x] Kubernetes delivery, scripted relay, cross-node recovery and observation linkage for the first real repository; no real-model result yet.
- [x] Run-bound model credentials, 17 real-cluster fake-credential checks and the real pilot launcher; retain the failed first attempt.
- [x] Four real-model branches on one development repository: the third run passes 13 checks with 160 KB inputs and four solved branches, covering native continuation, handoff and isolated grading.
- [x] Offline controls for all four development environments; tool-prefix and fresh-container native recovery tests without model calls.
- [x] Four development tasks under tool-prefix policy: sixteen observations, post hoc prefix grading and cost audit.
- [x] Real Codex cross-node recovery after executor loss, preserving the native session, code, and execution state and passing independent tests; see [recovery acceptance](m4-model-recovery.en.md).
- [x] Publish [demo and deployment](demo.en.md), bilingual capability-focused READMEs, and the [acceptance index](index.en.md), retaining historical records.
- [ ] Run the fixed 20-task formal real-model evaluation and complete its report.

**Acceptance evidence**: experiments can be repeated from their configuration; reliability and strategy effectiveness each have measured evidence; and every published result can be traced to raw records.

Phase-one deployment, real-service faults, zero-model cost and reporting boundaries are in the [M4 guide](m4.en.md). Multiple local k3d nodes do not establish multi-physical-host HA; checked items apply only to this scope.
Four development and twenty evaluation SWE-bench Verified tasks are frozen. Paired analysis and
offline positive/negative verifier controls for all four development tasks pass; see the
[task/statistics protocol](m4-benchmark.en.md). The [four-task development round](m4-tool-prefix-results.en.md) has sixteen graded branches. Development evidence does not complete M4.

## Later candidates

Broader tool coverage and time-triggered checkpoints, more agents, trajectory judges calibrated against labeled examples, training-segment selection, and learned handoff policies. These do not block M0–M4.

## Decisions to resolve before implementation

| Decision | Initial preference | Basis for the decision |
| --- | --- | --- |
| Harbor integration boundary | Reuse public agent/environment interfaces where possible | M0 verification of lifecycle control |
| Development task set | Small, reconstructible filesystem tasks | Clear recovery contracts and manageable pilot cost |
| File snapshots | Complete declared directories plus immutable manifests | Establish correctness before measuring incremental optimizations |
| Primary budget constraints | Configure money and wall-clock limits separately | Adapter admission and observation capabilities |
| Control language | Python | Harbor integration cost and typed contracts |
| First execution backend | Local containers | Validate the protocol before distributed faults |

Local repository controls, Kubernetes execution and the credential path remain documented in the
[repository guide](m4-repositories.en.md), [cluster guide](m4-repository-cluster.en.md), and
[historical pilots](m4-model-pilot.en.md). The subsequent [earlier-handoff round](m4-early-prefix.en.md) diagnoses failed traces and fixes the login shell: modified, unfinished prefixes increase from 1/4 to 2/4, with one workspace-relay rescue. Two ungraded timeout failures remain preserved.

The [runtime-notification and timeout revision](m4-runtime-notice.en.md) passes 56/56 workflow checks and four post hoc prefix diagnostics: native continuation solves 4/4 and all three mini strategies solve 2/4, with sixteen graded branches. All four prefixes are unfinished but unchanged; there are no partial-patch samples. Unfinished percentage alone therefore does not establish policy improvement. Real command-timeout continuation followed by a passing grade is verified.

M4 known API charges total $0.85497195, with zero unknown API requests, within the $30 authorization;
subscription cost is unknown. Real recovery acceptance adds $0 in API charges. Next, complete runtime
integration, environment preparation, and reporting for the fixed 20-task evaluation. Milestones are
accepted on functionality and reliability; paired outcomes do not prescribe a winner.
