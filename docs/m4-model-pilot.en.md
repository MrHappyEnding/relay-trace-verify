# M4: Model credentials and the first real repository pilot

[![中文](assets/lang-zh.svg)](m4-model-pilot.md) [![English](assets/lang-en.svg)](m4-model-pilot.en.md)

## 160 KB input policy (third run preparation)

The owner increased the cumulative API budget for this stage from $8 to **$20**, including
both previous attempts. The next run uses `mini-160kb-v1`: 160,000 input bytes, 4,096 output
tokens, $0.16 reserved per request, and at most 32 requests, for a **$5.12** run allowance.
Previous reservations are $1.178, so the full next allowance would total $6.298. Previous
known API cost is $0.04215240; reservations are not actual charges. Explicit execution
opt-ins remain required, with no automatic rerun.

This revises the transport envelope while retaining models, task, sampling protocol, time
limits, and branch order. The sampling protocol's $0.062 is the original planning estimate;
the frozen spec's `api_envelope`, run SHA, preparation file, and credential admission bind
the actual allowance. Historical specs without a policy retain `mini-40kb-v1` (40,000 bytes /
$0.062), including M3 defaults, and reconstruct unchanged. Per-run admission and the $20
cumulative stage budget are checked separately.

Model-free validation: 192 unit/mocked tests passed, with one real-container test requiring
explicit opt-in; Python 3.11 core tests passed 108 with 54 optional-dependency skips. Tests
cover sending a 45 KB input, stopping before a request/reservation above 160 KB, and rejecting
a lower reservation grant for the larger spec. The third real run is still pending.

**Status: the repaired second run passes 13 real-model workflow checks.** One SWE-bench development
task completed checkpointing, cross-agent handoff, native Codex continuation and independent grading
of four branches. M4 and formal effectiveness evaluation remain incomplete. This attempt made 13 API
requests for $0.02641425; both attempts total 19 requests and **$0.04215240** under the same $8 budget.
Codex subscription dollar cost remains unknown. No GPUs or cloud resources were used.
See the [second-run evidence](evidence/m4-real-pilot-2-arm64-20261012.json). The
[first failed attempt and fixes](evidence/m4-real-pilot-1-arm64-20261012.json) and
[fake-credential checks](evidence/m4-credentials-arm64-20261012.json) remain separate historical records.

## Second run: workflow acceptance and effectiveness limits

The owner explicitly requested one repaired rerun within the original $8 total budget. Execution
used frozen `model-pilot-prepared-3.json` without changing models, task or request caps, taking
556.04 seconds overall.

| Branch | Agent exit | Independent grade | API requests | Branch API USD |
| --- | --- | --- | ---: | ---: |
| A-continue | Native turn completed; same session ID | solved / 1 | 0 | Subscription cost unknown |
| B-workspace | `InputLimitExceeded` | solved / 1 | 6 | 0.01105530 |
| B-summary | `Submitted` | solved / 1 | 4 | 0.00727665 |
| B-fresh | `InputLimitExceeded` | unsolved / 0 | 3 | 0.00808230 |

B-workspace reached 40,699 bytes before request 7; B-fresh reached 41,695 bytes before request 4.
Neither oversized request was sent. Both retain runtime status `failed / agent_limit_or_error`,
but preserve their workspaces, trajectories and checkpoints and complete isolated verification.
Statistics therefore record solved and unsolved respectively. Agent exit and task correctness are
separate outcomes.

The 13 checks cover sealing the prefix without hidden tests, matching workspace restoration on all
three continuation branches, native session identity, four independent verifiers, observation linkage,
budget reservations and cleanup. During finalization the launcher cancels the already sealed source
Trial, so its final row says `cancelled`; its immutable checkpoint retains `sealed / stage_complete`.
This cleanup does not indicate a source-stage failure.

**All three continuation branches retained exactly the prefix's tracked Git patch; the substantive
repair was already present in the prefix.** This validates preservation and continued execution, not
a second agent rescuing an unfinished repair. B-fresh produced no tracked patch before its input cap.
Only one independent task is observed: 4 of 16 planned development observations, with 12 still missing
and confidence intervals withheld. The two debugging attempts are not independent samples. Model and
scaffold also change together, so this does not establish a success-rate or cost advantage for relay.

This attempt received only a $1.984 allowance and reserved $0.806 for actual requests. With the first
attempt's $0.372, cumulative reservations are **$1.178**, leaving **$6.822** under conservative non-reuse
accounting. All 13 API responses have settled usage. Owned Jobs, Pods, Secrets and resource reservations
are removed; all three nodes are Ready. A scan of 47,524 run artifact files found no original credential
values. Full trajectories and workspaces remain in ignored `work/`; Git stores reports, hashes and
provenance indexes only.

## First attempt and fixes

One frozen `sympy__sympy-22914` pilot ran on 2026-10-12. All outcomes are retained:

| Stage | Outcome | Cause |
| --- | --- | --- |
| Codex prefix | Infrastructure failure; no checkpoint | RTV's 60-second default for ordinary commands cut off Harbor's entire CLI execution before the approved 300-second stage deadline |
| B-summary / B-workspace / A-continue | `source_failed`; not launched | No sealed shared prefix |
| B-fresh | `infra_error`; ungraded | Request 7 reached 40,052 input bytes and was rejected before sending at the 40,000-byte cap; the original exception bypassed finalization and verification |

All six API requests have returned usage; their reservations total $0.372. No seventh API request
was sent. Stage-level UNKNOWN records remain, and Codex subscription cost is not zero. Owned Jobs,
Pods, Secrets and resource reservations were cleaned up; a scan of 33 run artifact files found no
credential values. Full trajectories remain in ignored `work/` only.

The fix gives Codex `exec` commands without an explicit timeout a fallback of the stage limit plus
30 seconds; the outer stage timer owns cancellation. Ordinary lifecycle commands remain bounded at
60 seconds and explicit tool timeouts stay unchanged. Mini now exits normally with
`InputLimitExceeded`, preserving its patch and trajectory for isolated grading without truncating
history, raising budgets or sending another request. Runtime status may still report an agent limit;
statistics use the independent verifier to assign solved / unsolved. This fixes stop handling;
context compression is not implemented and real tasks may still exhaust the input cap early.

The changes pass 189 unit/mocked tests (one container test requires opt-in), and 108 Python 3.11 core
tests (52 optional-dependency skips). Four real Docker timing checks pass without model calls; the
long command took 65.19 seconds. The repaired image matches all 52 workspace Python files, is
imported on three nodes, and passes 17 two-node fake-credential regression checks. Run the Docker probe with:

```bash
.venv/bin/python scripts/m4_execution_limits_probe.py \
  --contract work/m4-data/model-repository-package-1/contract.json \
  --output work/m4-control/execution-limits-probe-next
```

These were the zero-model checks before the owner-requested second run. The first failed run's
frozen image and results remain unchanged.

## Credentials and execution admission

The default pool has no `model_access` and still rejects real models. Only the explicitly approved
launcher creates two immutable Kubernetes Secrets and records **name + UID references** in that run's
configuration. Metadata contains neither credential contents nor their hashes. Secret values go
directly through the Kubernetes API, without shell arguments, shared-volume staging or Workflow
history. Launcher reports keep filtered types rather than raw authentication responses.

- Admission binds one runtime root, frozen run SHA, five Trial IDs, their spec hashes and request
  allowances. Controller and runner both validate these; only epoch 1 is accepted, with no automatic
  recovery or replay of model stages.
- Each Job mounts exactly one Secret for its adapter: read-only projection, `0400` file, no
  service-account token. Inherited model authentication variables are cleared before spawning the
  supervisor; only the admitted adapter receives opt-in and a file path.
- Mini's API key stays in the outer executor; Harbor executes tools in the network-disabled inner
  container. The existing Harbor Codex adapter copies its login cache into the inner temporary auth
  directory. Checkpoints export only the allowed files.
- A lost Secret-create acknowledgement is recovered by reading and checking the same immutable
  object, owner and contents, without a second create. Job admission checks Secret UID, and cleanup
  uses a UID precondition rather than deleting a replacement object.

Both nodes share one Docker Desktop host. Production executor Pods still use privileged DinD and
support trusted local experiments only. Secret volumes cannot pin object UIDs themselves; the
pre-launch check assumes trusted namespace administrators. This does not defend against cluster
administrators, hostile privileged processes or an agent reading its own auth file. Secret rotation,
persisting refreshed Codex credentials, multi-host secret management and encryption-at-rest setup
are not implemented. If the launcher crashes, use `credential-intent.json` and
`credential-references.json` to identify and clean up this run's Secrets; never bulk-delete unrelated
namespace credentials.

## Frozen experiment and cost

Both runs cover only `sympy__sympy-22914`, preserving the existing protocol, images, hidden-test
separation and four-arm order. Agents receive the public issue and selected handoff context, without
scripted repairs or the reference solution. Preparation does not access real credentials.

| Stage | Configuration | Limit |
| --- | --- | --- |
| Shared prefix | Codex `0.160.1` / `gpt-6-astra` | 300 seconds; sealed without hidden verification |
| A-continue | Same native Codex session | 300 seconds |
| B-workspace | mini-swe-agent `2.4.6` / `gpt-5.4-mini-2026-03-17` | 300 seconds / 8 requests |
| B-summary | Same, with an unverified summary | 300 seconds / 8 requests |
| B-fresh | Same, starting from the original repository | 600 seconds / 16 requests |

Each Trial has a total 1800-second deadline including queueing, preparation and verification; a pool
runs one Job at a time. Source failures are retained and the fresh baseline still runs in its frozen
order. Infrastructure failures do not trigger automatic reruns. Real and oracle observations are
exported separately. Collection checks actual specs, context policies, operation types, checkpoints
and prefix accounting. Incomplete verification is not a graded outcome. An honest unsolved model
result remains an observation; the launcher does not retry until the repair succeeds.

The [official OpenAI pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini), checked on
2026-10-12, lists $0.75 input, $0.075 cached input and $4.50 output per million tokens. This transport
allows 40,000 input bytes and 4,096 output tokens per request, reserving a non-reusable $0.062.
Each run allows up to 32 requests reserving **$1.984**. Both attempts share the owner's **$8 total
API budget** and leave the remaining budget unused. This is local
admission accounting, not a provider invoice guarantee. Two Codex stages additionally consume
existing subscription allowance; their dollar cost stays unknown rather than free. No cloud
resources are provisioned. Authentication follows the
[official OpenAI authentication documentation](https://learn.chatgpt.com/docs/auth); M3's API transport
and Codex adapter are reused without a model upgrade.

## Usage

First prepare the pinned Codex CLI agent image/contract using the
[local repository guide](m4-repositories.en.md), then deliver a package with `cluster prepare-repository`.
It contains that agent image, the independent verifier and network sidecar. Keep the local PostgreSQL
port-forward and Temporal service running. Package and runtime paths must be on the cluster volume.

```bash
# No model calls or credentials: freeze a plan using fresh plan and runtime paths.
.venv/bin/python scripts/m4_model_pilot.py prepare \
  --config work/m4-data/model-repository-package-1/config-limits-v1.json \
  --contract work/m4-data/model-repository-package-1/contract.json \
  --root work/m4-data/real-model-pilot-next \
  --output work/m4-control/model-pilot-next.json

# Only after owner approval. Credentials must be private regular files outside the shared volume.
.venv/bin/python scripts/m4_model_pilot.py run \
  --prepared work/m4-control/model-pilot-next.json \
  --auth-file "$HOME/.codex/auth.json" \
  --api-key-file work/openai-api-key.txt \
  --allow-model-calls --allow-api-calls --approved-budget-usd 2
```

Execution exports `run.json`, ledgers, observations and checks, then cleans up owned Jobs, Pods and
Secrets. `benchmark collect-run` can re-export observations without launching anything. Failed
artifacts remain in ignored `work/`; a new attempt needs a new plan and a remaining-budget review.

The fake-credential acceptance script, `scripts/m4_credentials_acceptance.py`, runs a replacement
entrypoint on both nodes to check Secret delivery, permissions, adapter-specific variables, no model
client import, log leakage and cleanup. It does not run the production supervisor or a model. The
first attempt encountered the Kubernetes Python client's JSON-log-to-dictionary-to-string conversion;
reading raw log bytes fixed it. That failed attempt is retained.

`model-pilot-prepared-3.json` has completed; do not reuse its runtime root. Next, prioritize zero-model
preflights for the other three development environments and design a comparison with unfinished work
at the prefix boundary. Freeze a new protocol before changing stage budgets or context policies;
never edit past records. The evaluation set, real-model fault experiments and full report remain
outstanding. Review remaining authorization before future paid runs; a third attempt is not started
automatically.
