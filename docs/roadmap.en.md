# RTV Roadmap and Acceptance Criteria

[![中文](assets/lang-zh.svg)](roadmap.md) [![English](assets/lang-en.svg)](roadmap.en.md)

Status: Draft · 2026-10-07

The initial framework completes F0 below. M0–M4 remain pending and are ordered to reduce risk. The local fixture demo does not replace compatibility testing with real agents.

## F0: Framework skeleton

- [x] Python package, `rtv` CLI, and uv dependency lockfile.
- [x] Trial state transitions, Attempt/Execution identities, and adapter contracts.
- [x] Local file checkpoints, hash validation, and restoration into independent directories.
- [x] JSONL events, a two-stage fixture relay, and a final verification report.
- [x] Tests for states, isolation, corruption detection, and commit failure windows.

See the [framework development guide](framework.en.md) for the current implementation boundary.

## M0: Compatibility and protocol prototype

- [ ] Pin Harbor, candidate agent CLIs, model identifiers, and container runtime versions.
- [ ] Run one minimal task and establish the task/agent/sandbox/verifier integration boundary.
- [ ] Verify workspace export at stage completion, restoration into a new sandbox, native-session import, and ATIF import.
- [ ] Verify agent subprocess termination and quiescence boundaries; produce an evidence-based capability matrix.
- [ ] Define snapshot file scope and handling of permissions, symbolic links, and required generated files.
- [ ] Identify adapters that support strict budget admission and those that support only soft limits.

**Acceptance evidence**: a recovery example between two isolated sandboxes, file-digest comparisons, compatibility records, and explicit rejection of unsupported capabilities. M0 does not require a complex web interface or Kubernetes.

## M1: Durable single-agent execution

- [ ] Trial/Attempt/Execution objects, state transitions, and stable operation IDs.
- [ ] Temporal Workflows and control Activities whose results can be queried and safely retried.
- [ ] Submission, status queries, cancellation, final verification, and artifact persistence.
- [ ] Link control events, execution events, costs, and results from day one.
- [ ] Reconciliation and sandbox takeover after worker restart, TTLs, and cleanup retries.

**Acceptance evidence**: duplicate submissions do not create duplicate valid executions; cancellation converges; existing sandboxes can be reconciled after restart; and tests leave no task resources behind.

## M2: Checkpoints and reliable recovery

- [ ] Manifest schema, content validation, quiescence barrier, and immutable artifact storage.
- [ ] Conditional checkpoint commits, epoch fencing, and recovery into independent workspaces.
- [ ] Handling of unresolved tool operations and UNKNOWN costs.
- [ ] Fault injection at commit windows, during disconnection, and when old workers return.
- [ ] Collection of unreferenced artifacts and protection of branch references.

**Acceptance evidence**: incomplete artifacts cannot be restored; committed files match after recovery; old epochs cannot publish; cumulative cost does not reset; and lost-work windows are recorded.

## M3: Two-agent handoff and experiment branching

- [ ] Idempotent Handoff, target preparation, and transfer of execution control.
- [ ] Task-only, summary, and portable-trajectory context strategies.
- [ ] Isolated branches from the same checkpoint.
- [ ] Paired experiments, prefix-cost attribution, and independent verification.
- [ ] Minimal interface for state, checkpoint lineage, handoff trajectories, and results.

**Acceptance evidence**: A-continue, B-workspace, and B-summary can be rerun; branches cannot contaminate one another's files; source state, costs, and final verification are fully traceable.

## M4: Multi-worker validation and complete report

- [ ] Kubernetes or a selected remote execution backend; exactly one lifecycle owner for each resource.
- [ ] Bounded concurrency, resource admission, recovery after node/worker loss, and cleanup.
- [ ] Freeze the formal task set, budgets, exclusion rules, and statistical methods in advance.
- [ ] Run real-model experiments and system fault experiments; produce raw artifacts and reports.
- [ ] Document deployment steps, operating costs, known limitations, and the full demonstration.

**Acceptance evidence**: experiments can be repeated from their configuration; reliability and strategy effectiveness each have measured evidence; and every published result can be traced to raw records.

## Later candidates

Tool-boundary checkpoints, more agents, trajectory judges calibrated against labeled examples, training-segment selection, and learned handoff policies. These do not block M0–M4.

## Decisions to resolve before implementation

| Decision | Initial preference | Basis for the decision |
| --- | --- | --- |
| Harbor integration boundary | Reuse public agent/environment interfaces where possible | M0 verification of lifecycle control |
| Development task set | Small, reconstructible filesystem tasks | Clear recovery contracts and manageable pilot cost |
| File snapshots | Complete declared directories plus immutable manifests | Establish correctness before measuring incremental optimizations |
| Primary budget constraints | Configure money and wall-clock limits separately | Adapter admission and observation capabilities |
| Control language | Python | Harbor integration cost and typed contracts |
| First execution backend | Local containers | Validate the protocol before distributed faults |

The next implementation task is the M0 compatibility prototype. Do not expand into complex scheduling policies before the capability matrix has supporting evidence.
