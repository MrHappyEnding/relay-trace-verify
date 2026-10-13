# M4: development preflights and mid-task handoff

2026-10-12: all four verifier environments pass, and the new policy has completed its
[four-task real-model round](m4-tool-prefix-results.md): sixteen observations, fourteen workflow
checks per task, and four post hoc offline prefix grades. This page describes the mechanism and its
earlier zero-model tests; formal evaluation remains incomplete.

## Development preflights

| Task | Unmodified reward | Reference-patch reward | This run |
| --- | ---: | ---: | ---: |
| sympy__sympy-22914 | 0 | 1 | Previously completed |
| sympy__sympy-15599 | 0 | 1 | 56.12 s |
| sympy__sympy-13091 | 0 | 1 | 41.33 s |
| scikit-learn__scikit-learn-9288 | 0 | 1 | 119.21 s |

The three new tasks use pinned Harbor files and AMD64 base images. Each offline control checks the
parser, reward and exit status. All six control containers were removed; derived images, dependency
locks and raw logs remain under ignored `work/`. See [new evidence](evidence/m4-development-preflights-arm64-20261012.json)
and the [first task](evidence/m4-benchmark-preflight-arm64-20261011.json). These are environment/verifier
controls, not agent outcomes. No evaluation-task outcomes were inspected and no tasks were replaced.

## Boundary semantics

The new development [protocol](../configs/benchmarks/swebench-verified-tool-prefix-v1.json) admits at
most **six Bash/apply_patch tools** in the prefix, retaining the 300-second stage cap. Six is an initial
development setting, not a finalized evaluation policy. The historical natural-turn protocol and all
three prior runs remain unchanged.

1. Synchronous PreToolUse hooks count admissions under a file lock and deny tools beyond the limit.
2. Already-started shell processes may finish via write_stdin. PostToolUse records matching completion
   receipts and requests a final handoff message.
3. The agent ends its turn normally with an unverified summary. Denial does not immediately stop
   inference; refusal to finish before the stage timeout fails without a prefix checkpoint.
4. The host verifies start/stop receipts, session/turn identity, tool records and the native rollout.
   Missing hooks, duplicate/unmatched receipts, unsupported tools, surviving processes or incompatible
   versions prevent sealing.
5. The runner stops the sandbox, verifies quiescence and commits the workspace, native session,
   receipts and summary through the existing ownership protocol. Continuations share that checkpoint
   and do not inherit its tool gate.

Early natural completion, including zero tools, is retained as `natural_completion`; reaching the
allowance is `tool_budget`. Hidden grading never selects prefixes. A tool allowance does not bound
tokens, API requests, subprocess counts or dollars. One shell call can do substantial work, so six
tools can still solve the issue. Development runs must measure this.

Prefix/continuation/fresh caps remain 300/300/600 seconds; API request caps remain 8/8/16. Unused prefix
time is not transferred. Report actual prefix time, usage, handoff overhead and failures; identical
caps do not imply identical compute. Model/scaffold and summary confounding still apply.

## Attribution and scope

The implementation adapts [official Codex hooks](https://learn.chatgpt.com/docs/hooks) and Harbor
0.24.0's Codex adapter. RTV supplies the allowance protocol, receipt reconciliation, process check
and checkpoint linkage. Codex CLI 0.160.1 was tested directly; other versions are not certified.

This is cooperative yielding for trusted development tasks. Hooks can fail open, so checkpoint
eligibility is independently audited; they are not isolation against malicious agents. Process
checks detect new live processes in the container, not external side effects. No process memory is
restored, and arbitrary instruction-level interrupts do not become recoverable checkpoints. Hook
trust is bypassed only for the vetted RTV hook uploaded into the disposable sandbox; host settings
are unchanged.

## Validation and reproduction

218 unit/mocked tests pass; one older container test remains explicitly opt-in. The Python 3.11 core
suite passes 131 tests with 55 optional-dependency skips. Checks cover concurrency, exhaustion, early completion, missing receipts,
identity mismatch, unfinished tools, surviving processes and protocol changes.

The [offline CLI probe](evidence/m4-tool-prefix-offline-arm64-20261012.json) supplies scripted local
responses, executes and waits for an asynchronous shell, applies a patch, denies the next tool,
commits/restores the workspace after sandbox exit, and continues the same native session in a fresh
container. Six checks pass and both containers are removed. Scripted responses do not establish real
model behavior or new Kubernetes/mini relay effectiveness. The first probe's restore script failed
to overwrite read-only Git objects; that record is retained. Replacing the fresh container's base
directory fixed the probe.

```bash
.venv/bin/python scripts/m4_tool_prefix_probe.py \
  --image sha256:617df833249959a54991bca0a5128bceb3fa84fb12884361eeaa1e3d6d3a595f \
  --output work/m4-tool-prefix-new
```

The image must already exist locally. No credentials are read/mounted or real provider contacted.
Use a fresh output directory. Before real development runs, build/import a runner containing this
code and prepare a new pool config as described in the pilot guide. An old runner must not execute
the new protocol. Identical tasks/environments may reuse prior preflights and task contracts, while
protocol SHA, run SHA and credential grants change.

```bash
.venv/bin/python scripts/m4_model_pilot.py prepare \
  --config <new-runner-pool-config> --contract <prepared-task-contract> \
  --protocol configs/benchmarks/swebench-verified-tool-prefix-v1.json \
  --api-envelope mini-160kb-v1 \
  --root <fresh-runtime-root> --output <fresh-plan-file>
```

The approved four-task round is complete. See [results and costs](m4-tool-prefix-results.md).
Next, analyze failed continuations and regressions, improve unfinished-prefix coverage under a new
preregistered development policy, then freeze the final evaluation strategy. The twenty-task evaluation
and real-model fault experiments remain pending.
