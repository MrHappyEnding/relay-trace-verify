# RTV Learning, Reproduction, and Resources

Updated: 2026-10-08

## Purpose

RTV supports learning through reproduction, engineering implementation, and original
exploration. Existing solutions are references and baselines, not reasons to remove
features. Record sources, reproduced scope, modifications, and validation evidence
separately. Engineering depth and research novelty are distinct; a scripted demo
does not establish real-model performance.

## Reading and reproduction plan

Except for the Harbor checks explicitly recorded in M0, these experiments have not
yet been reproduced in RTV.

| Reference | Learning or reproduction target | RTV area |
| --- | --- | --- |
| [Harbor](https://docs.harborframework.com/jobs/loading-trajectories) | Task/environment/verifier integration; separately test native sessions, ATIF, and files | M0, M3 |
| [Temporal Activities](https://docs.temporal.io/activity-definition) | Retry after a lost completion acknowledgment; idempotency and reconciliation | M1, M2 |
| [LangGraph time travel](https://docs.langchain.com/oss/python/langgraph/use-time-travel) | Branch graph checkpoints; understand external-environment boundaries | M3 comparison |
| [Handoff Debt paper](https://arxiv.org/abs/2606.02875) / [code](https://github.com/anjilab/agent-handoff-debt) | Small-scale reproduction of handoff views with fixed repository state; retain same-agent continuation | M3, M4 |
| [Crab paper](https://arxiv.org/abs/2604.28138) / [code](https://github.com/open-agent-infra/crab) | Read and run filesystem/process recovery examples; examine external effects | Recovery study; current M2 does not promise process-memory recovery |
| [AgentFS](https://github.com/tursodatabase/agentfs) | File, state, and tool-record storage; compare with full-directory snapshots | Storage study |
| [OpenHands SDK](https://github.com/OpenHands/software-agent-sdk) | Conversation lifecycle, persistence, and workspace interfaces | Adapter reference |
| [AgentRx paper](https://arxiv.org/abs/2602.02475) / [code](https://github.com/microsoft/AgentRx) | Constraints, evidence logs, failure localization, and annotation calibration | Later Trace extension |
| [DeltaBox](https://arxiv.org/abs/2605.22781) | Incremental filesystem/process checkpoints; assess a deeper performance reproduction | Later systems study |

AgentScaling and AgentTracer in the initial discussion came from descriptions
provided by the owner. Original code/paper sources have not been verified. Retain
them as inspiration, not validated performance evidence.

## Resources and spending

- The owner has access to four lab H100 GPUs. VRAM, host architecture, SSH,
  scheduler, networking, storage quotas, and usage rules remain unconfigured.
  RTV has not connected to the lab or started jobs there.
- Model serving and task sandboxes may run separately. Use deterministic fixtures
  for development and fault tests. Self-hosted models are an optional later backend;
  compatibility with any particular agent CLI must be tested.
- Before new paid API calls, cloud resources, or other incremental charges, explain
  purpose, estimated cost, run size, and stopping conditions, then obtain owner
  confirmation. A budget estimate does not authorize spending.
- M0 preparation makes no model calls and provisions no paid cloud resources.
  Public dependencies and base images still consume bandwidth and local storage.
- The earlier 400–650-hour estimate for M0–M4 is a planning assumption, not a
  delivery promise. Re-estimate cash costs after a self-hosted pilot; commercial
  API prices do not represent the lab's incremental cash costs.

## Reproduction deliverables

Record pinned dependencies/configuration, upstream version, commands and samples,
raw-artifact references, differences, and failure cases. Label small-sample
reproduction, full-paper reproduction, porting, and original extensions separately.
Upstream results remain attributed to their authors; only measured RTV results
may be presented as RTV evidence.
