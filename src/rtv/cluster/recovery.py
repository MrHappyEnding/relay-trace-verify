"""A reproducible reliability run, separate from paired benchmark observations."""

from pathlib import Path

from rtv.benchmark.protocol import digest
from rtv.runtime.handoff import target_spec
from rtv.runtime.relay_store import stable
from rtv.runtime.store import identity


def prepare_run(source_spec, root):
    from rtv.cluster.credentials import validate_model_spec

    root = str(Path(root).resolve())
    source = {
        **source_spec,
        "stages": 2,
        "pause_after_checkpoint": True,
        "max_recoveries": 0,
        "agent_seconds": 300,
        "ttl_seconds": 1800,
        "api_budget_usd": 0,
        "reliability_assignment": "codex-recovery-v1:source",
    }
    if source.get("adapter") != "codex" or "benchmark_assignment" in source:
        raise ValueError("Recovery trials are Codex-only and separate from benchmark observations")
    if "prefix_policy" in source:
        raise ValueError("Recovery acceptance uses a natural checkpoint, not prefix selection")
    validate_model_spec(source)
    target = {
        **target_spec(source, "codex", source["model"], "native", ttl=1800),
        "agent_seconds": 300,
        "max_recoveries": 1,
        "api_budget_usd": 0,
        "reliability_assignment": "codex-recovery-v1:target",
    }
    validate_model_spec(target, allow_recovery=True)
    source_key = "codex-recovery-v1:source"
    target_key, label = "codex-recovery-v1:experiment", "native-recovery"
    experiment = stable(root + ":experiment:" + target_key)
    body = {
        "schema_version": 1,
        "mode": "real-model-recovery",
        "root": root,
        "fault": "delete_executor_job_after_native_codex_process_observed",
        "maximum_codex_stages": 3,
        "maximum_api_requests": 0,
        "source": {
            "key": source_key,
            "trial_id": identity(root, source_key)["trial_id"],
            "spec": source,
        },
        "entries": [
            {
                "key": target_key,
                "label": label,
                "experiment_id": experiment,
                "trial_id": identity(root, experiment + ":" + label)["trial_id"],
                "policy": "native",
                "spec": target,
            }
        ],
    }
    return {**body, "run_sha256": digest(body)}


def validate_run(run):
    if prepare_run(run["source"]["spec"], run["root"]) != run:
        raise ValueError("Recovery run differs from its frozen assignment")
