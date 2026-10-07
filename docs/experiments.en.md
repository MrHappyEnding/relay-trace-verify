# RTV Experiment Protocol Draft

[![中文](assets/lang-zh.svg)](experiments.md) [![English](assets/lang-en.svg)](experiments.en.md)

Status: Draft · 2026-10-07

## 1. Answer two separate questions

1. **System reliability**: after interruptions, duplicate delivery, and cancellation, does the system maintain its invariants, and what does recovery cost?
2. **Strategy effectiveness**: under a predefined budget, does handoff improve task success rate, cost, or completion time?

A reliable system does not automatically make handoff effective. Individual successful handoffs do not establish the reliability of the recovery protocol.

## 2. Paired checkpoint experiments

Produce checkpoint C using a predefined switching rule, then create branches with isolated filesystems. Start with the first three primary branches; add the others as the budget permits.

| Branch | Workspace | Context | Purpose |
| --- | --- | --- | --- |
| A-continue | C | A's native session | Primary baseline: continue with the original agent |
| B-workspace | C | Task instructions | Primary branch: change agent while retaining existing work |
| B-summary | C | Task instructions and structured handoff | Primary branch: incremental value of the summary |
| A-fresh | C | Task instructions | Context-reset ablation |
| B-trajectory | C | Portable trajectory | Full-history versus summary comparison |

Also evaluate an end-to-end baseline where B starts from the task's initial state. First keep the scaffold fixed while changing the model, then keep the model fixed while changing the scaffold. Changing both at once only measures their combined effect.

Do not select checkpoints as suitable for handoff based on final hidden-test results. Triggers may use only observations available at runtime, such as a fixed step count, public tests, an explicit stage boundary, or a budget threshold.

## 3. Budgets and costs

- Choose the primary constraint in advance: a dollar budget or a wall-clock budget. Equal token counts do not establish cost fairness across models.
- Give every continuation branch from C the same remaining budget. Account consistently for conversion, summary generation, verification, and environment startup.
- Report both incremental cost after the checkpoint and end-to-end cost including A's prefix.
- Prefix computation may be shared to reduce experimental spending, but each strategy's estimated deployment cost must include that prefix.
- Give the B-from-scratch baseline its predefined full budget. Do not conflate it with branches that have already consumed a prefix budget.
- Report model, container, storage, and network costs separately. Retain UNKNOWN entries and estimated ranges for unconfirmed model spending.
- Retain costs caused by infrastructure failures; do not silently remove them from totals.

## 4. Verifier isolation

The online agent and scheduling policy may use only public tests. Run hidden tests during final evaluation. Their contents, answers, and feedback must not enter handoff packages or be used to tune policies on the evaluation set.

A separate verifier environment requires explicit transfer and digest validation of declared artifacts. Do not assume it inherits the agent's entire filesystem. Record test failures, verifier execution errors, and infrastructure failures separately.

## 5. Samples and statistics

Start with approximately 10 tasks to validate collection, budgeting, and reproducibility. These are development tasks, not evidence of a general improvement.

Choose the formal task count and repetitions based on pilot cost and outcome variance. Freeze the development/evaluation split, task versions, exclusion rules, and experiment configuration. Repetition identifiers support pairing; they do not imply that a remote model supports deterministic seeds.

Report per-task outcomes and success-rate differences. Construct confidence intervals with the task as the clustering unit, preserving dependencies among branches, checkpoints, and repetitions of the same task. Separately report samples without valid results because of infrastructure failures, rerun rules, and the end-to-end failure rate.

## 6. System experiments

For each fault-injection point, record its location, repetition count, recovery path, and invariant-check results.

- Checkpoint commit: before upload, during upload, after manifest completion, and after database commit but before the response.
- Ownership: worker disconnection, late return of an old worker, duplicate starts, and duplicate completion messages.
- Cancellation: while queued, restoring, executing a tool, or preparing a handoff target.
- Resources: container loss, worker restart, and retries after cleanup failure.

Metrics: recovery success rate and sample count; recovery latency p50/p95; lost-work window; checkpoint size and commit time; invalid stale-result count; duplicate external-operation count; cancellation convergence time; and residual resource count.

Increase concurrency gradually in scale tests. Record host and worker configuration, queue wait, throughput, and resource usage. Present simulated-agent and real-model results separately.

## 7. Artifacts for each experiment

```text
experiment-config.json       # Fixed tasks, branches, budgets, versions, and repetition IDs
provenance.json              # Source, image, adapter, verifier, and configuration digests
trials/<trial-id>/            # Raw trajectories, artifact references, ledger, results, and fault records
summary.csv                  # Per-trial data derived from raw records
report.md                    # Methods, metrics, confidence intervals, failures, and limitations
```

Keep large artifacts in object storage or a local artifact directory, outside Git. Reports must retain machine-readable content digests and access instructions.
