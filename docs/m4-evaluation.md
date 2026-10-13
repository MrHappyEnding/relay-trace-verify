# M4: executing the frozen evaluation set

The evaluation launcher uses the frozen 20-task SWE-bench Verified subset: two independent prefixes
per task and four branches per prefix. Tasks, order and repeats come from the
[v1 protocol](../configs/benchmarks/swebench-verified-v1.json), without outcome-based replacements.
Paired results describe execution strategies; platform acceptance checks handoff, isolated grading,
accounting and cleanup.

## Execution and spending

[`scripts/m4_evaluation.py`](../scripts/m4_evaluation.py) prepares environments, submits runs,
settles spending and archives artifacts serially. It freezes all 160 assignments, source fingerprints,
runner image and cumulative budget before launching models. Durable admission precedes model execution.
Known charges settle against actual usage; unknown requests retain their reservation. An interrupted,
unsettled run prevents further spending. Restarting the coordinator never automatically replays a
submitted paid run.

The existing `mini-160kb-v1` envelope admits at most 32 API requests at $0.16 each, reserving $5.12
per pair. Settlement releases unused reservations, so the sum of every pair's worst-case envelope
is not treated as an actual invoice. The cumulative limit and prior spending are explicit arguments.
Codex subscription usage is recorded separately without inventing a dollar price.

```bash
.venv/bin/python scripts/m4_evaluation.py \
  --config work/executor-config.json \
  --root work/m4-data/evaluation \
  --control work/evaluation-control \
  --environments work/evaluation-environments \
  --upstream work/m4-benchmark/harbor-datasets \
  --auth-file /absolute/private/codex-auth.json \
  --api-key-file /absolute/private/openai-api-key.txt \
  --approved-total-usd 30 --prior-cost-usd 0.85497195 \
  --stop-after-task django__django-11163
```

Paths are deployment placeholders. The configuration must identify the deployed dedicated cluster,
immutable runner image and shared volume. `--stop-after-task` stops after both repeats of that task.
It limits this execution without changing the full frozen schedule; other tasks remain unrun.
Omit the option to execute the full schedule.

## Offline grading and artifacts

Each task first undergoes offline unmodified-code and reference-patch verifier controls. These check
the environment and are not model results. Some upstream graders fetch public requirements files
inside `make_test_spec`. Image preparation caches original HTTP responses at fixed Git commit URLs.
Runtime replay validates SHA256 and rejects uncached or unpinned requests. The original grading script
and tests remain unchanged. Grading configuration is supplied through a read-only build mount and
is not stored in the agent image.

Each run retains raw ledgers, trajectory references and observations. After allocation release,
workspace and checkpoint directories are compressed. A readback checks file bytes, file permissions
and symbolic links before removing expanded copies. `archive.json` records the archive hash and
restoration command. Rebuildable image delivery archives are released after both task repeats settle;
image identities and delivery hashes remain available.

Progress lives in `progress.json` in the control directory; immutable reports live in `reports/`.
Missing outcomes, environment problems, unsolved model attempts and harness checks remain distinct.

An archive mismatch stops cleanup. If a verified archive succeeds but deletion of expanded copies fails,
the archive and remaining copies are retained with `cleanup_complete: false` in `archive.json`.
Settled model runs are never replayed because of cache cleanup.
