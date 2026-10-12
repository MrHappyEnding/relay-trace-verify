# M4: Real-repository tasks and paired analysis

[![中文](assets/lang-zh.svg)](m4-benchmark.md) [![English](assets/lang-en.svg)](m4-benchmark.en.md)

**Status: frozen task selection, statistical protocol and offline analysis are implemented;
real-container verifier controls pass for one development task.** Real-model SWE-bench experiments
have not run. Subsequent [cluster repository work](m4-repository-cluster.en.md) verifies scripted
relay and recovery. This stage makes zero API calls
and provisions no cloud resources. M4 as a whole remains in progress.

## Sources and frozen identity

Tasks come from [SWE-bench Verified](https://huggingface.co/datasets/princeton-nlp/SWE-bench_Verified),
using tasks, images, tests and reference solutions produced by the
[Harbor SWE-bench adapter](https://github.com/harbor-framework/harbor/blob/main/adapters/swebench/README.md).
RTV implements selection, paired identities, failure retention, cost attribution and analysis;
upstream tasks and verification are reused contributions.

| Source | Frozen identity |
| --- | --- |
| Verified data | `c104f840cc67f8b6eec6f759ebc8b2693d585d4a`, all 500 metadata rows |
| Harbor tasks | `laude-institute/harbor-datasets`, `86723674f04e4209ac479d0fb75d9d9f44b4377e` |
| Harbor runtime | `0.24.0` |
| Environments | 24 individual `linux/amd64` image manifest digests |
| Protocol SHA256 | `dbd68d56849681a5ac991409743d0276ee8b5572992c3570a18ae532ab4cd8bb` |

The [frozen configuration](../configs/benchmarks/swebench-verified-v1.json) records base commits,
image identities and SHA256 hashes for 144 upstream files. The
[catalog](../configs/benchmarks/swebench-verified-catalog.json) contains only task IDs, repositories,
base commits and provenance. Downloaded prompts, solutions, hidden tests and full artifacts remain
under ignored `work/`.

Rank by `sha256("rtv-m4-swe-v1:" + instance_id)` before observing outcomes. The first four tasks are
development tasks; the next twenty are evaluation tasks. Development IDs are `sympy__sympy-22914`,
`sympy__sympy-15599`, `sympy__sympy-13091` and `scikit-learn__scikit-learn-9288`.
Splits are disjoint. Do not replace tasks based on difficulty, compatibility or results; unsupported
environments remain recorded failures. Protocol changes require a new identity without overwriting
the old configuration.

## Experimental protocol

Each task/repeat forms a paired block. A commits a checkpoint after one naturally completed stage
within 300 seconds. Hidden rewards never determine fork eligibility. Prefix timeout gives all three
continuation arms `source_failed` while retaining costs; the fresh baseline can run independently.
The summary is the source agent's final message, labeled unverified, without an extra model call.

| Branch | Initial state and context | Execution cap |
| --- | --- | --- |
| A-continue | Shared checkpoint and native A session | 300 seconds |
| B-workspace | Shared checkpoint and original task | 300 seconds / 8 API requests |
| B-summary | Shared checkpoint, original task and unverified summary | 300 seconds / 8 API requests |
| B-fresh | Original repository and task; no checkpoint or prefix cost | 600 seconds / 16 API requests |

The development split has one repeat: 4 prefixes and 16 branches. Evaluation has two repeats:
40 prefixes and 160 branches. A stable hash sets the within-block order; branches use independent
workspaces. Queue time is reported separately from execution caps. Caps do not imply equal actual
consumption; the runtime must preserve measured durations and usage.

The primary contrast is **B-summary minus A-continue**; other contrasts are exploratory descriptions.
Planned A: Codex CLI 0.160.1 / `gpt-6-astra`. Planned B: mini-swe-agent 2.4.6 /
`gpt-5.4-mini-2026-03-17`. Model and scaffold both change, so this comparison measures a combined
strategy effect. B-summary versus B-workspace more directly isolates added summary context.
B-fresh compares end-to-end strategies, not continuation from a common checkpoint.
These are pending runtime configurations; the analysis CLI never launches models.

## Statistics and accounting

- All planned observations remain in the denominator. Infrastructure failures, cancellations and
  prefix failures are unsuccessful in ITT resolution, with reasons and graded-only denominators
  reported separately.
- Missing rows become explicit `missing` observations. Affected comparisons are provisional with
  no confidence interval; a wholly missing comparison has a null difference.
- Average paired differences across repeats within each task, then bootstrap **task clusters**
  10,000 times with seed 20261011 for percentile 95% intervals. Repeats and branches are not independent
  samples. Twenty tasks support exploration, not an official 500-task score or population superiority.
- Reports require a homogeneous `scripted`, `oracle` or `real-model` mode. Duplicate observations,
  forged pairing fields, inconsistent checkpoints and reward/outcome conflicts are rejected.
- Unknown interrupted cost stays null with an UNKNOWN count and a known-cost lower bound. Each
  continuation strategy includes its prefix; do not sum duplicated prefix attribution across physical
  forks as an invoice. Subscription usage is separate: zero API spend does not mean free inference.
- Input validation checks consistency, not authenticity. The runtime must connect observations to
  logs, ledgers and artifacts. Automatic latency, token and recovery summaries await runtime integration.

The existing M3 transport envelope reserves $0.062 per request **for planning only**, not as a current
price quote, invoice guarantee or M4 authorization:

| Scope | Maximum API requests | API reservation | Codex subscription stages |
| --- | ---: | ---: | ---: |
| Four development tasks | 128 | $7.936 | 8 |
| Twenty tasks, two repeats | 1,280 | $79.36 | 80 |

Validate the actual transport and current prices, then obtain approval for a concrete paid run.
The prior M3 $8 approval does not cover M4. Local image downloads use existing network/storage;
no cloud resources or lab GPUs are launched.

## Model-free commands

Run from the checkout root. Metadata audit, scheduling and analysis support Python 3.11 core;
materialization additionally needs Git and network access:

```bash
.venv/bin/python -m rtv benchmark plan
.venv/bin/python -m rtv benchmark schedule --split evaluation
.venv/bin/python -m rtv benchmark budget --split pilot
.venv/bin/python -m rtv benchmark materialize --output work/m4-benchmark/harbor-datasets
.venv/bin/python -m rtv benchmark audit --upstream work/m4-benchmark/harbor-datasets
```

`materialize` pins the upstream commit, checks out the 24 directories sparsely, then audits every file.
An existing checkout with a different revision or changed content fails. Supply `--plan` before the
subcommand to select an explicit configuration. The default targets this checkout; wheel installs
should supply the configuration and adjacent catalog explicitly.

Container preflight requires Docker and accepts development tasks only. Build time installs verifier
dependencies and creates a lock; verification runs offline. Upstream packaging commits may differ
from the base commit only when the code tree is identical and the worktree is clean:

```bash
.venv/bin/python scripts/m4_benchmark_preflight.py \
  --upstream work/m4-benchmark/harbor-datasets --task sympy__sympy-22914 \
  --output work/m4-benchmark/preflight-new
```

Two isolated containers test unmodified code and the upstream reference patch. Parser output, reward
and exit status must agree; a parser failure producing zero cannot pass the negative control.
Only owned containers are removed. Images, dependency lock, logs and hashes remain for inspection.
The initial image build needs network access. The lock records this resolution; reproducing the
derived environment across machines requires preserving the image or reusing its lock. Pinning only
the base image does not make the entire build byte-for-byte reproducible.

Scripted statistical acceptance, export and input examples:

```bash
.venv/bin/python scripts/m4_benchmark_acceptance.py --output work/m4-benchmark/statistics-new
.venv/bin/python -m rtv benchmark analyze --split pilot --mode scripted \
  --records work/m4-benchmark/statistics-new/records.jsonl --output work/m4-benchmark/report-new
```

Each JSONL row requires its scheduled observation ID, protocol SHA, task ID, repeat and branch,
plus `mode`, `status`, `checkpoint_id`, `prefix_api_cost_usd`, `incremental_api_cost_usd` and
`unknown_operations`. Graded `solved/unsolved` rows also require `verifier_status="completed"` and
matching integer `verifier_reward=1/0`. Other statuses are `infra_error/source_failed/cancelled`.
Output directories must be new. Exports contain raw JSONL with its SHA256, the frozen protocol,
full JSON/CSV and Chinese/English Markdown reports.

## Evidence and remaining integration

See [container preflight evidence](evidence/m4-benchmark-preflight-arm64-20261011.json) and
[statistical acceptance evidence](evidence/m4-benchmark-protocol-20261011.json). On the ARM64 macOS
host, the first development task runs as `linux/amd64`: unmodified reward 0, reference-patch reward 1,
both containers removed. This validates one task environment and verifier, without agents or
Kubernetes. Earlier permission, packaging-commit and exit-code adaptation failures are retained.

The local runtime now supports declared `/testbed` tasks, checkpoint export, isolated restoration,
hidden-test separation, a 600-second verifier and a 16-request fresh entry point; see
[repository integration](m4-repositories.en.md). Evidence uses scripted/reference controls, not real
model sessions. Kubernetes resources, AMD64 execution, task delivery and observation linkage are covered
in [cluster repository integration](m4-repository-cluster.en.md). Credential delivery, the other three
pilot preflights and real-session compatibility remain pending before budget-approved model experiments.
