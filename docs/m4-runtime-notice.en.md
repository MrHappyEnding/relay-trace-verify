# M4: runtime handoff notification and command timeout

[![中文](assets/lang-zh.svg)](m4-runtime-notice.md) [![English](assets/lang-en.svg)](m4-runtime-notice.en.md)

## Frozen before round three

[Round two](m4-early-prefix.en.md) produced 2/4 unfinished prefixes and one heterogeneous rescue,
plus two ungraded tool-timeout branches. Announcing a numerical allowance may encourage packing
reads, edits, and tests into one tool; two tools are not a fixed amount of task progress. The new
[tool-prefix-v3 protocol](../configs/benchmarks/swebench-verified-tool-prefix-v3.json) retains two
admissions for every source, but the initial prompt only mentions a possible handoff notice.
PostToolUse requests completion at the boundary; excess calls are denied. Quotas, completed-tool
receipts, and workspace quiescence remain audited. Natural completion is retained. Prefixes with
no code changes will be reported as such, not described as partial-patch handoffs.

Mini repository tools run through `/usr/bin/timeout --signal=TERM --kill-after=2s 30s bash -lc`.
The command process group receives TERM at thirty seconds and, if needed, KILL two seconds later.
Exit codes 124/137 become ordinary tool observations for the agent. Transport waits forty seconds
and the thread fifty; genuine transport exceptions remain infrastructure errors without transparent
retry. All four fixed images contain GNU timeout. A model-free real-container probe confirms a
TERM-resistant command and descendant are terminated without subsequent workspace writes.

This jointly changes notification and timeout behavior, so before/after results cannot isolate
their causal effects. The four tasks, models, task/verifier images, 300/300/600-second limits, and
8/8/16 API caps remain fixed. No prior patches or hidden feedback enter the task packet. Each task
runs once; source prefixes are graded offline only after every branch finishes. No outcome-based
selection, substitution, automatic paid retry, or twenty-task evaluation execution. Earlier
versions, raw observations, and results remain preserved.

A separate analysis correction retains known cost lower bounds from infrastructure-error rows
while preserving unknown-total counters. Request ledgers and admission limits are unchanged.

## Execution gates and budget

The unit/mocked suite passes 225 tests with one optional Docker skip. A real-CLI, scripted-model,
offline container probe passes six checks including the two-tool boundary, excess-call denial,
session restoration, and cleanup. These are not real-model efficacy evidence. The new runner
must bind a source commit and image digest and pass seventeen fake-credential checks before use.

The existing user authorization allows up to $30 without another confirmation. Prior known M4
API spending is $0.57644430 with zero unknown API requests. This round reserves up to $20.48 for
128 calls, giving an initial cost-plus-reservation bound of $21.05644430. Unknown/pending requests
retain their full reservations. Subscription cost remains unknown and separate; historical gross
reservations are not invoices. New paid calls stop if they would exceed authorization.

Real-model results await this round. Local orchestration scripts and full trajectories remain in
ignored `work/`.
