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

Results and filtered evidence will be appended after completion. Full trajectories and
credentials remain in ignored `work/`.
