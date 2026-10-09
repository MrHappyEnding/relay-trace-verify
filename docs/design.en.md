# RTV System Design Proposal

[![中文](assets/lang-zh.svg)](design.md) [![English](assets/lang-en.svg)](design.en.md)

Status: Draft · 2026-10-07

## 1. Goals and guarantee scope

Build an execution system with consistent checkpoints, heterogeneous agent handoffs, and branching experiments. The first version supports only tasks whose writes can be stopped at a declared quiescent boundary and whose environments can be reconstructed from pinned images and declared files.

Recovery targets the last committed application-level checkpoint. Uncommitted work may be lost, and that loss window must be observable. Checkpoints do not preserve arbitrary process memory, network connections, or undeclared external-service state.

Reproducibility means traceable configurations, inputs, and artifacts, with an environment that meets the recovery contract. It does not guarantee identical model output.

## 2. State objects

| Object | Definition | Relationships and identifiers |
| --- | --- | --- |
| Experiment | An experiment with fixed tasks, strategies, budget accounting, and repetitions | `experiment_id`, immutable configuration digest |
| Trial | One complete task-solving run for an experiment arm and repetition | `trial_id`, cumulative budget and final result |
| Attempt | A logical execution stage assigned to one agent/model/scaffold configuration | `attempt_id`; a Trial may contain several Attempts |
| Execution | One physical execution instance of an Attempt | `execution_id`, monotonically increasing `epoch`, independent sandbox |
| Checkpoint | Immutable artifacts and a manifest describing a quiescent state | `checkpoint_id`, digest, source execution, and event boundary |
| Handoff | A durable transfer operation from a source Attempt's checkpoint to a target Attempt | `handoff_id`, source checkpoint, and target configuration |
| Operation | A reconcilable action such as sandbox creation, startup, termination, or commit | Stable `operation_id`, unchanged across retries |
| Event | A durable record from the control plane or execution | Source execution, source sequence, type, timestamp, and artifact references |

**Recovery** preserves the Attempt ID and creates a new Execution/epoch. **Handoff** creates a new Attempt. **Experiment branching** creates a new Trial and an independent budget ledger, recording `parent_checkpoint_id` and prefix cost; it is not an automatic retry of the original Trial.

Do not use a Temporal Activity attempt number as the product-level Attempt ID.

## 3. Required invariants

1. Only committed checkpoints with validated contents may be used for recovery or handoff.
2. Within an Attempt, only the current epoch may commit authoritative state and valid results.
3. Different Executions use independent writable workspaces by default; experiment branches must not share writable directories.
4. Once cancellation takes effect, reject new execution admissions and agent-stage starts. Cleanup may retry asynchronously.
5. The Trial cost ledger accumulates across recovery and handoff; rebuilding an Execution does not reset it.
6. Every valid result is traceable to its task version, execution configuration, workspace artifacts, and verifier version.
7. Record result status and resource-cleanup status separately. Completion does not imply sandbox deletion.

## 4. State ownership

Temporal owns workflow execution history. PostgreSQL owns product objects, cancellation intent, ownership epochs, budget admission, and artifact-commit records. Workflows access these records through Activities with stable operation IDs; Workflow code does not perform I/O directly.

Do not assume distributed atomicity from dual writes to the database and Temporal. Operations committed to the database can be queried on retry. Reconciliation continues to resolve incomplete cross-system operations. The API presents product state with an explicit synchronization time.

Minimal state sketches:

```text
Trial: QUEUED -> RUNNING -> VERIFYING -> SUCCEEDED | FAILED
          |         |          |
          +---------+----------+-> CANCEL_REQUESTED -> CANCELLED

Execution: ALLOCATING -> RESTORING -> RUNNING -> QUIESCING -> STOPPED
                       \-------------> LOST | FAILED

Checkpoint: PREPARING -> UPLOADING -> COMMITTED
                      \-----------> ABORTED

Handoff: REQUESTED -> SOURCE_SEALED -> TARGET_READY -> COMPLETED
                   \------------------------------> ABORTED
```

Use conditional state updates that check the epoch and cancellation flag. Record failure categories as structured fields instead of treating every timeout as an agent failure.

## 5. Checkpoint commit protocol

### 5.1 Safe boundaries

Support checkpoints at stage completion first. Enable tool-boundary checkpoints only when the adapter can demonstrate quiescence. Stopping the main agent process alone does not prove that child processes or background services have stopped writing.

Quiescence requires stopping new tool requests, waiting for or terminating active tools and related subprocesses, flushing controlled logs, and confirming that the declared file scope is no longer being written. If this cannot be confirmed, reject the new checkpoint and retain the last valid one.

The prototype must establish whether process-group and sandbox-lifecycle controls are sufficient to achieve quiescence. Unsupported tasks must fail explicitly.

### 5.2 Manifest contents

- Schema version, checkpoint ID, and source Trial/Attempt/Execution/epoch.
- Task digest, starting code revision, image digest, and environment/recovery configuration digests.
- Snapshot inventory for the declared file scope, file-content hashes, permissions, and symbolic-link semantics.
- Session-snapshot reference, format, agent/scaffold versions, converter version, and known information loss.
- Complete boundaries of acknowledged tool records and execution events, not merely the maximum sequence number.
- Public-test result references and the workspace digests they describe. Do not treat results from an older workspace as current evidence.
- Budget-ledger revision and references to consumed, reserved, and unresolved entries.
- Parent checkpoint and creation reason.

A Git diff alone is not a complete file snapshot. Include new files, required generated artifacts, and declared mounts. Reinject credentials at runtime rather than placing them in portable artifacts.

### 5.3 Commit steps

1. Check current execution ownership and cancellation intent; enter QUIESCING.
2. Complete the quiescence barrier and establish a common boundary for files, session state, and events.
3. Upload immutable file, session, and event artifacts; compute and validate their digests.
4. Upload the immutable manifest and verify that all references are readable and consistent.
5. Conditionally commit the checkpoint and update the relevant pointers in a database transaction, checking the epoch and cancellation flag.
6. Return a stable operation result. If the response is lost, retrieve the committed record using the same operation ID.

Object storage and the database do not require a cross-system transaction: artifacts become recoverable only after step 5 succeeds. Collect unreferenced artifacts after a retention period. Garbage collection must protect uploads still being committed and references from all live branches.

## 6. Recovery and handoff

### 6.1 Recovery

1. Identify the failure category. Use distinct policies for infrastructure retries and continued agent attempts.
2. Atomically acquire a new epoch and create a new sandbox with a stable operation ID.
3. Validate the manifest, reconstruct the image and declared files, and verify the workspace digest.
4. Import the native session. If unsupported, use only an explicitly labeled semantic recovery policy.
5. Reconcile pending Operations and budget entries. Record actions whose completion cannot be established as UNKNOWN.
6. Check cancellation again before starting the agent.

An old execution that reconnects may produce only stale observations, not valid state commits. Epoch checks cannot prevent disk writes, so old and new executions must not share writable workspaces. Independent reconciliation continues terminating and reclaiming old sandboxes.

### 6.2 Handoff

A handoff is defined by its source checkpoint, target configuration, context-transfer strategy, and budget limit. Commit the transfer of control only after the target is ready and its restored state has been validated. Duplicate delivery of one handoff ID must not create a second valid target Attempt.

Supported context strategies are task instructions, portable trajectories, and structured summaries. Summaries must distinguish observed facts, test evidence, attempted approaches, and agent hypotheses, with references to original events. Include summary generation and conversion costs in the total budget.

Changing an agent or model does not imply tool compatibility, native-session compatibility, or identical context semantics.

## 7. Adapter capability contract

These capabilities must be probed and tested; they are not guarantees about any particular agent's current implementation.

| Capability | Initial policy |
| --- | --- |
| Startup, result collection, and status reconciliation | Required; normalize exit states and error categories |
| Process-group termination and quiescence evidence | Required; reject checkpoints if quiescence cannot be demonstrated |
| Stage-boundary checkpoints | Required |
| Tool-boundary checkpoints | Optional, later extension |
| Native-session export/import | Explicit declaration, subject to version compatibility |
| ATIF output/loading | Declare separately; output support does not imply import support |
| Incremental events and token/cost accounting | Label completeness and estimation sources |
| Request-cost upper bounds and call interception | Enforce strict admission when supported; otherwise declare soft limits |

The first verified integration is Codex CLI 0.160.1 + `gpt-6-astra` through Harbor 0.24.0; see the [experiment](codex-m0.en.md). M3 pins mini-swe-agent 2.4.6 as the second scaffold. Its scripted and real-API loops are verified, including three context policies and native A continuation on one development task. See [M3](m3.en.md). Start with stage-boundary handoffs rather than assuming every CLI can pause after any tool call.

## 8. Budget model

The Trial ledger contains `reserved`, `settled`, and `unknown` entries, with model and infrastructure costs recorded separately. Ledger updates for each operation are idempotent. Actual duplicate requests must be accounted for individually; deduplication must not hide real spending.

For adapters that can intercept requests, reserve an upper cost bound before sending a request. Settle against returned usage and release the difference. If the response is lost, retain the unresolved reservation rather than assuming zero consumption.

For CLIs whose internal requests cannot be intercepted, enforce only soft limits through native controls, observation, and termination. Documentation and metrics must disclose the possible overshoot window; do not claim a strict monetary cap.

Constrain infrastructure allocation through resource quotas and runtime TTLs. Billing is a delayed observation. In-flight requests may still incur costs after cancellation.

## 9. Failure-handling matrix

| Injection point or failure | Expected handling | Core assertion |
| --- | --- | --- |
| Sandbox created but acknowledgement lost | Query by operation ID and take over | No second valid execution |
| File upload interrupted | Leave checkpoint uncommitted | Recovery cannot observe partial artifacts |
| Checkpoint committed in the database but response lost | Retrieve the same result on retry | No duplicate state advancement |
| Old worker returns after heartbeat timeout | Epoch fencing and stale-result isolation | Old epochs cannot publish |
| Container destroyed | Restore committed checkpoint into a new sandbox | Declared files and session satisfy the contract |
| Cancellation races with startup or handoff | Check persistent cancellation intent at admission points | No new valid stage; eventual cleanup |
| Model response lost | UNKNOWN; reconcile or retry under policy | Costs are not incorrectly released |
| Hidden verifier fails or times out | Separate classification and bounded retries | Not directly counted as an agent task failure |

## 10. Events and diagnosis

Control events cover receipt, admission, startup, disconnection, cancellation, checkpoints, handoffs, verification, and cleanup. Tool events link calls with their results. Handle duplicate delivery with source sequence numbers and idempotency keys; do not rely on wall clocks across machines for global ordering.

Raw artifacts are immutable; query projections can be rebuilt. Store large logs in object storage, outside Temporal history. Begin with failure classification, repeated operations, public-test changes, and lost-work windows. Calibrate process judges later.

## 11. Dependency verification references

- [Harbor loading trajectories](https://docs.harborframework.com/jobs/loading-trajectories)
- [Harbor multi-step tasks](https://docs.harborframework.com/tasks/multi-step)
- [Harbor separate verifier](https://docs.harborframework.com/tasks/separate-verifier)
- [Temporal Activity definition](https://docs.temporal.io/activity-definition)

These documents inform the research. The quiescence, snapshot, and ownership protocols described here are RTV proposals that require empirical validation.
