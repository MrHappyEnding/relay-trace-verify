# M4: Four-task tool-prefix experiment

2026-10-12: each of four fixed development tasks ran once, producing **16 real-model observations**.
All 14 workflow checks pass on each task. Native continuation solves 4/4, summary relay 3/4,
workspace relay 2/4, and fresh execution 2/4. **Execution and handoff work; heterogeneous relay
superiority is not established.** These are not official SWE-bench Verified scores. The twenty-task
evaluation split has not run, and M4 is not complete.

See the [full evidence](evidence/m4-tool-prefix-development-arm64-20261012.json),
[mechanism](m4-tool-prefix.md), and [historical natural-turn pilots](m4-model-pilot.md).

## Complete results

Prefixes were graded separately in fresh, offline containers only after all paid branches finished,
with observations collected and resources released. These grades were never fed to agents or used to
filter tasks or change allowances. 1 means the verifier passed; 0 means it did not.

| Development task | Prefix | A native | B workspace | B workspace + summary | B fresh |
| --- | ---: | ---: | ---: | ---: | ---: |
| sympy__sympy-22914 | 1 | 1 | 1 | 1 | 1 |
| sympy__sympy-15599 | 1 | 1 | 0 | 1 | 0 |
| sympy__sympy-13091 | 0 | 1 | 0 | 0 | 0 |
| scikit-learn__scikit-learn-9288 | 1 | 1 | 1 | 1 | 1 |

- The three SymPy prefixes sealed after six completed tools. Scikit-learn completed naturally after
  five and was retained as specified. Receipt, native-rollout and process-boundary audits passed for
  all four; no hidden verifier ran before sealing.
- All three continuations restore their shared source checkpoint. All four A branches preserve the
  native session ID. Each of the twenty model stages runs once, without automatic paid retries.
  All sixteen isolated verifiers complete normally.
- Both mini continuations on sympy-13091 stop at eight requests with `LimitsExceeded` and fail grading.
  Scikit-learn's summary branch also exhausts its allowance but passes grading. Agent termination and
  repair correctness remain distinct fields.
- Every API input fits the frozen 160,000-byte envelope; the maximum is 70,887 bytes. No input-limit
  stop occurs in this round.

## Interpretation

**Sympy-13091 is the only unfinished prefix.** Native continuation changes its grade from 0 to 1;
neither heterogeneous continuation does so. This demonstrates native continuation completing an
unfinished repair. Heterogeneous rescue is 0/1; the other three already-passing prefixes cannot count
as rescue successes.

Workspace relay on sympy-15599 introduces a regression: it changes distribution conditions in
`sympy/core/mod.py`, turning the prefix's passing grade into a failure. Native continuation and summary
relay retain the same tracked patch and pass. A successor can damage existing progress; the precise
cause and better regression controls need further analysis.

There are **four independent tasks**, not sixteen independent samples. The primary comparison,
summary relay minus native continuation, is -25 percentage points, with an exploratory task-paired
bootstrap 95% interval of [-75, 0] points. This development sample does not establish population
superiority. Model and scaffold change together, and subscription costs are unknown, preventing a
claim of lower end-to-end cost. All successes, failures and pairing records remain in the analysis.

## Costs and environment

| Strategy | API requests | Known API USD |
| --- | ---: | ---: |
| A native continuation | 0 | Codex subscription; dollar cost unknown |
| B workspace | 28 | 0.06962625 |
| B workspace + summary | 26 | 0.06024660 |
| B fresh | 37 | 0.09234165 |
| Round total | 91 | **0.22221450** |

Four additional Codex prefix stages bring subscription stages to eight; these are not priced as free
inference. The owner approved an approximately **$25.82** cumulative reservation ceiling. This round
allowed at most 128 API requests and $20.48 in reservations. Issued requests reserve $14.56; together
with historical $5.338, non-reusable request reservations total $19.898, leaving $5.922.
M4 totals 136 API requests and **$0.33947070** in known charges, with no UNKNOWN API receipts.
Reservations and charges are separate. Pricing follows the [OpenAI model documentation](https://developers.openai.com/api/docs/models/gpt-5.4-mini);
these figures do not guarantee the final invoice.

The four runs total 2,274.39 seconds, approximately 37.9 minutes, excluding image preparation and
post hoc diagnosis. Three k3d nodes share one ARM64 Docker Desktop host; task containers run AMD64.
Tasks and branches execute serially. No lab GPU or paid cloud resource was used. This does not test
availability across physical hosts.

Paid runs execute code at `0cd6832`; the runner is pinned to
`sha256:6122ab477b150e9a279b03d4a9a2818774ba386d3187e9e22815f13cc5d09091`.
All 53 Python implementation files match the host, and all three nodes use the same digest. The new
runner passes 17 fake-credential checks across both executor nodes. Unit/mocked tests pass 218 checks;
one opt-in legacy Docker test is skipped. Jobs, experiment Pods, model Secrets, diagnostic containers
and resource allocations are cleaned up, with all nodes Ready. Full trajectories, workspaces and
account metadata remain in ignored `work/`; Git contains selected evidence and source only.

## Reproduce post hoc prefix diagnosis

The new [m4_prefix_diagnostics.py](../scripts/m4_prefix_diagnostics.py) validates frozen identities,
collection of four observations, and released allocations for every supplied run before restoring
manifest-verified checkpoints and invoking the same offline isolated verifier. It reads no model
credentials, submits no Trial and provides no agent feedback. A fresh output directory is required.
An unfinished round was verified to be rejected before any container starts. All four real diagnostics
pass integrity checks and leave their source workspaces unchanged.

```bash
.venv/bin/python scripts/m4_prefix_diagnostics.py \
  --roots work/m4-data/tool-prefix-round-1/sympy__sympy-22914 \
          work/m4-data/tool-prefix-round-1/sympy__sympy-15599 \
          work/m4-data/tool-prefix-round-1/sympy__sympy-13091 \
          work/m4-data/tool-prefix-round-1/scikit-learn__scikit-learn-9288 \
  --output work/m4-prefix-diagnostics-new
```

This needs retained local run artifacts, task packages and images. The script validates the caller's
declared round, not other unlisted concurrent experiments. Evidence includes settings, source hashes,
per-stage usage, paired analysis and diagnostic grades. The three historical natural-turn pilots
remain separate.

## Next work

Analyze the sympy-13091 failures and sympy-15599 regression, then preregister an earlier fixed tool
boundary while holding other variables unchanged, to improve coverage of unfinished prefixes in the
development set. Any mini request-cap change should be a separate comparison; more requests are not
assumed to solve failures. Freeze the final policy before the twenty-task evaluation and real-model
fault experiments. The four approved tasks are complete; no additional paid run was started.
