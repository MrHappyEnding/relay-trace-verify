# M4: earlier handoff and execution-environment correction

[![中文](assets/lang-zh.svg)](m4-early-prefix.md) [![English](assets/lang-en.svg)](m4-early-prefix.en.md)

## Frozen before execution (2026-10-12)

The [previous round](m4-tool-prefix-results.en.md) left only one of four prefixes unfinished.
This revision keeps the same tasks, models, 300/300/600-second limits, and mini's 8/8/16
request caps, while moving every source prefix from six to **two tool admissions**.
The development traces first edited code at tools 4, 3, 4, and 3. The new boundary is fixed
before execution, without inspecting new hidden grades. Natural early completion remains
included; two tools do not guarantee unfinished work, and a tool can perform several operations.

Mini repository commands also switch to `bash -lc`. Harbor's ordinary `exec` uses `bash -c`,
which does not load these images' login configuration. Previously mini's `python` selected
base Python 3.11, while Codex's default login shell selected `testbed`. SymPy branches lacked
mpmath, and old SymPy additionally encountered Python 3.11's `collections.Mapping`
incompatibility. The fix loads existing image configuration without installing dependencies
or adding solution hints, uniformly across mini repository arms. Quoting the complete action
preserves multiline commands and exit status.

This is a **joint development revision**, changing the prefix allowance and correcting the
shell. Before/after results cannot isolate their causal effects. The separate
[tool-prefix-v2 protocol](../configs/benchmarks/swebench-verified-tool-prefix-v2.json) preserves
all earlier protocols, traces, and results. Execution binds a new runner digest while reusing
the exact task and verifier images.

## Observations from failed trajectories

| Earlier branch | Observation | Interpretation limit |
| --- | --- | --- |
| sympy-15599 workspace relay | Tool 1 cannot import mpmath; tool 3 broadens the modulo distribution condition; only compilation is checked afterward | Introduces a regression; environment trouble impedes feedback but is not proven the sole cause |
| sympy-15599 fresh | After finding testbed, public `test_Mod` still fails an assertion; the agent submits anyway | Does not repair observed test failure; 14 requests remain below the cap of 16 |
| sympy-13091 both continuations | Both exhaust eight requests, encounter test-environment errors, and focus on Basic/Expr without numbers.py overrides | Both wasted environment effort and incomplete repair coverage; not attributable solely to the cap |
| sympy-13091 fresh | Edits only Basic comparisons, cannot run the public example with missing dependencies, compiles and submits | Six requests; budget exhaustion does not explain this failure |
| sympy-13091 native continuation | Extends the repair to numeric-type overrides in numbers.py and corresponding tests; isolated grading passes | One successful completion path, not proof that all other repairs must fail |

These are manual observations from local records, not a calibrated automatic root-cause
classifier. Prior solutions, gold patches, and hidden-test feedback are not injected into
the new agents' task packets.

## Validation and spending constraint

Unit/mocked tests precede model-free probes in all four actual task images: login Python
identity, project imports, quoting/multiline semantics, and nonzero exits. The new runner
also passes fake-credential projection/cleanup acceptance. Each fixed task then runs once;
all sixteen branches finish and clean up before offline grading of the four source checkpoints.
No outcome-based substitution, prefix selection, or automatic paid retry. The twenty-task
evaluation split remains unexecuted.

The owner authorizes up to $30. All 136 historical M4 API requests have known usage, costing
$0.33947070. This round allows at most 128 requests, retaining the conservative $0.16 admission
reservation per request: at most $20.48. The round-level accounting is historical settled
cost plus full reservations for unknown/pending requests plus the new round's worst-case
allowance, initially $20.81947070, below $30. The historical $19.898 gross reservations are
not an invoice; earlier non-reusable-reservation reports remain unchanged and are reported
separately. Unknown responses never release reservations, and within-run limits are unchanged.
Subscription usage is separate with unknown dollar cost; recorded API pricing is not an
invoice guarantee.

## Round-two results (2026-10-12)

The filtered [evidence](evidence/m4-early-prefix-development-arm64-20261012.json) binds runtime
commit `3de0652e1245c6a950d73d124798973f6febfc71`. Four tasks ran once: fourteen of sixteen branches
received independent grades; two remained ungraded infrastructure errors. Execution checks passed
54/56. Failed records remain intact, with no automatic retry. All four prefixes admitted and
completed two tools and contained code changes. Prefixes were graded only after every branch ended.

| Task | Source prefix | A native continuation | B workspace | B workspace + summary | B fresh |
| --- | --- | --- | --- | --- | --- |
| sympy-22914 | Pass | Pass | Pass | Pass | Fail |
| sympy-15599 | Pass | Pass | Pass | Pass | Fail |
| sympy-13091 | Fail | Pass | Fail | Fail | Fail |
| scikit-learn-9288 | Fail | Pass | Pass | Ungraded: infrastructure | Ungraded: infrastructure |

Unfinished-prefix coverage is **2/4**, versus 1/4 previously. Within those two tasks, native
continuation rescues 2/2, workspace relay 1/2, and workspace-plus-summary 0/2 including one ungraded
branch. The KMeans parallel random-seed task supplies one real heterogeneous rescue, not evidence
of overall superiority over native continuation. Repeated development on four tasks is exploratory;
sixteen branches are not sixteen independent tasks. The twenty-task evaluation split remains unrun.

The corrected shell removes the observed SymPy mpmath/Mapping errors, but incomplete repairs
persist: mini's 13091 changes still focus on Basic; the fresh 15599 branch submits despite a public
floating-point assertion failure; fresh 22914 exhausts sixteen requests without solving the issue.
Environment and input allowance therefore do not explain all failures.

The scikit-learn summary and fresh arms abort when a public command exceeds Harbor's 30-second
transport timeout, leaving no gradable artifact. All four and six respective API requests have
known usage; an unknown parent operation is not an unknown API invoice. The next version will
enforce command timeouts inside the container process group and return ordinary exit feedback,
while retaining genuine transport failures. A separate analysis fix includes known cost lower
bounds from infrastructure-error rows without zero-filling unknown totals. This evidence recomputes
the aggregate with that fix, preserves raw observations and the earlier aggregate copy, and records
the analysis implementation hash separately from the executed runtime.

The round made 99 API requests costing **$0.23697360**, with no unknown API requests. Cumulative
M4 totals are 235 requests and **$0.57644430** known cost. Historical gross reservations of $35.738
sum admission bounds, not actual spending. Eight subscription stages remain separately accounted
with unknown dollar cost. Task execution took about 43.5 minutes in total. A scan found no original
credential-value matches; this does not assert that full trajectories contain no account metadata.
Task Jobs, model Secrets, and diagnostic containers were removed; Postgres remains. The pre-run
unit/mocked suite passed 218 tests with one optional skip. Subsequent corrections pass 225 with
one optional skip; those later checks are not attributed to the already executed runtime.

The next development protocol is [runtime notification and command timeout](m4-runtime-notice.en.md).
