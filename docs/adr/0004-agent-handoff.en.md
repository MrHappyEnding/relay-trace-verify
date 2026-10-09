# ADR 0004: Stage handoff, paired branches and request accounting

[![中文](../assets/lang-zh.svg)](0004-agent-handoff.md) [![English](../assets/lang-en.svg)](0004-agent-handoff.en.md)

Status: implemented; real two-agent local development-task acceptance passed · 2026-10-09

## Problem

M2 can recover an Attempt but cannot compare continued execution by the original agent with a
different agent taking over. Shared writable directories contaminate branches. Unqualified summaries
mix verified facts with source-agent judgments. Copying cumulative usage double-counts the prefix.

## Decision

1. Seal the source at a completed stage. Handoff creates a new Attempt; recovery retains it. Relay
   keeps the Trial; experimental forks create independent Trials. Task and immutable image stay fixed.
2. Persist preparation before activating the target in PostgreSQL. Request keys, context digest,
   previous identity and epoch constrain the transfer. Prepared proves local input readiness only;
   sandbox creation follows activation.
3. Separate native, task-only, summary and portable policies. Cross-framework trajectories are text
   material, not imported foreign tool state. Summary uses the source final statement without another
   model call and labels it as self-report.
4. Freeze common prefix operation IDs. Each branch records incremental usage and reports prefix plus
   increment. Task, image and wall-clock allocation are equal. API limits apply only to mini; Codex
   and mini do not have identical hard token/dollar limits. Nested forks are deferred.
5. Use a fresh, network-disabled verifier sandbox with only the workspace. Record workspace and
   test digests in the result.
6. Implement mini Model/Environment interfaces on the host for pre-request admission and observed
   usage. Tools remain in Harbor Docker. Unknown requests are not retried and their reservations
   cannot be reused.

## Reuse, adaptation and original implementation

| Source | Reused or studied | RTV adaptation and limits |
| --- | --- | --- |
| [mini-swe-agent v2.4.6](https://github.com/SWE-agent/mini-swe-agent/tree/v2.4.6) | DefaultAgent, templates, Responses action parser, observations and output submission protocol | Host Model and Harbor Environment; upstream loop is reused, upstream local shell is not enabled |
| [Harbor ATIF](https://docs.harborframework.com/agents/atif) | Source trajectory format and Codex converter | Lossy text projection; no target-native import |
| [Harbor trajectory loading](https://docs.harborframework.com/jobs/loading-trajectories) | Model trajectory loading separately from environment restore | RTV owns checkpoint manifests and independent workspaces |
| [Temporal Activities](https://docs.temporal.io/activity-definition) | Durable control orchestration and retryable Activities | RTV owns business keys, authority transactions, non-replayed API requests and attribution |
| [OpenAI Responses](https://developers.openai.com/api/reference/resources/responses/methods/create) | Responses and usage | Pinned model, output limits, no SDK retries and database reservations; no universal invoice guarantee |

Handoff records, source reference protection, failure convergence and prefix attribution are engineering
implementations in this repository. They are not claims to have invented checkpoints, preparation
protocols or agent handoffs. Prior mechanisms remain as learning and reproduction work.

## Consequences and evidence

The local protocol passes 48 zero-cost checks, alongside 14 passing real-model checks. Evidence
separates real services, scripted models, synthetic accounting and real usage; see [M3](../m3.en.md). Both scaffold and model change on one development task;
this validates functionality, not framework or relay performance. Hidden verifier feedback is not
passed to B. Agent-issued test observations can appear in portable context, a policy difference to
freeze and record in formal experiments.

M3 uses schema `rtv3` and a new task queue without migrating old histories; M2 histories are not claimed
to be replay-compatible with M3. Artifacts remain local. Multi-host resource fencing and cancellation
of provider requests already in flight are outside the guarantee.
