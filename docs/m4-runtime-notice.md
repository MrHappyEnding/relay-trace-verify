# M4: runtime handoff notification and command timeout

## Frozen before round three

[Round two](m4-early-prefix.md) produced 2/4 unfinished prefixes and one heterogeneous rescue,
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

## Real round-three results (2026-10-13)

The filtered [evidence](evidence/m4-runtime-notice-development-arm64-20261013.json) binds runtime
commit `0a707c7db53d96ba26cf3828aeb8f54637de244d` and runner digest
`sha256:ddb0551c72b03d71ed2b43a3f14b7399156938230b995c90217c8215f5dc6cf1`.
Each of four tasks ran once: all sixteen branches received independent grades, **56/56 workflow
checks passed**, and no branch remained ungraded due to infrastructure. No automatic paid retries.
Every source sealed after two completed tools. Only after all branches and cleanup did checkpoint
validation, offline prefix grading, and source-unchanged checks eventually complete for all four
checkpoints. Two initial post hoc diagnostics fail Git ownership checks on restored files. Trusting
only `/testbed` inside each disposable verifier allows two model-free follow-ups to complete. Both
initial failures and follow-ups remain recorded with separate verifier implementation hashes. The
sixteen original branch grades remain unchanged, with no model reruns, task-byte or grading-rule changes.

| Task | Source prefix | A native continuation | B workspace | B workspace + summary | B fresh |
| --- | --- | --- | --- | --- | --- |
| sympy-22914 | Fail | Pass | Pass | Pass | Pass |
| sympy-15599 | Fail | Pass | Fail | Fail | Fail |
| sympy-13091 | Fail | Pass | Fail | Fail | Fail |
| scikit-learn-9288 | Fail | Pass | Pass | Pass | Pass |

Native continuation solves 4/4; each mini strategy solves 2/4. Unfinished-prefix coverage is now
**4/4**, but **none contains code changes**. This does not improve coverage of inherited partial
patches. Workspace relay has no modified code to inherit here; summaries and native sessions still
carry reading context. The three development revisions must be viewed together:

| Development version | Unfinished prefixes | Modified and unfinished prefixes | B workspace rescues within that stratum |
| --- | --- | --- | --- |
| v1: six announced tools | 1/4 | 1/4 | 0/1 |
| v2: two announced tools + login shell | 2/4 | 2/4 | 1/2 |
| v3: two runtime-notified tools + command timeout | 4/4 | 0/4 | No such samples |

The v2 scikit-learn task remains one real example of heterogeneous repair of a modified, unfinished
workspace. Version three validates earlier context transfer and timeout handling without adding
partial-patch samples. These are joint development revisions on the same four tasks, not isolated
ablations or generalization evidence. Relay has not shown overall superiority to native continuation,
nor has summary relay shown a success-rate advantage over workspace or fresh execution. Model and
scaffold change together, and subscription cost is unknown, precluding end-to-end savings claims.

### Real timeouts and failed trajectories

The scikit-learn summary arm encounters a thirty-second command timeout, receives exit code 124,
and completes another model response before its patch passes independent grading. It also records
`LimitsExceeded` after eight requests: execution termination and patch correctness are separate
outcomes. The previous round's same arm aborted on transport timeout without a grade. This round
retains a gradable artifact, validating the actual command-timeout continuation path, not recovery
from arbitrary transport failures.

All three mini arms on 15599 still broaden the modulo transformation condition. Workspace and
fresh arms observe a public `test_Mod` floating-point assertion failure, but their compound shell
continues to Git commands after Python fails and returns zero overall. A successful tool exit is
therefore not a substitute for reading test output or independent grading. Fresh submits after six
requests, below its cap. On 13091, all mini arms still focus on Basic; the passing native repair also
covers Expr and numbers.py. Several mini arms try unavailable pytest; that public-command choice is
not automatically a verifier infrastructure failure. These findings combine trajectory markers and
manual patch inspection, not calibrated automatic causal diagnosis.

### Costs, validation, and next step

This round makes 98 API requests costing **$0.27852765**. M4 totals 333 requests and **$0.85497195**
known API charges, with zero unknown API requests, within the existing $30 authorization. Eight
subscription stages remain separately accounted with unknown dollar cost. Historical gross
reservations of $51.418 sum admission bounds across rounds, not spending. Task execution totals
about 48.8 minutes. Sent requests retain the 160 KB input ceiling without enlarging this round's caps.

The pre-run unit/mocked suite passes 225 tests with one optional skip; seventeen fake-credential
checks and model-free real-container probes pass. A post-run statistics edge-case fix prefers a
fully known total over a smaller known lower bound; 226 tests pass with one optional skip, and ruff
checks pass. The same 226-test suite passes again after the verifier ownership fix. This round's
complete aggregate is identical before and after the cost correction. Runtime
and postprocessing implementation hashes and the earlier aggregate are separate; raw runs remain intact.

An original-credential-value scan finds no matches. Full trajectories remain in ignored `work/`;
this does not assert absence of account metadata. Jobs, model Secrets, and diagnostic containers
are cleaned up. M4 remains incomplete: the twenty-task evaluation and formal real-model fault
experiments have not run. Next, freeze a prefix-sampling rule covering multiple progress levels and
report unchanged versus modified workspaces separately before formal evaluation. Optimizing only
unfinished percentage is insufficient; hidden grades must not select which prefixes are handed off.
