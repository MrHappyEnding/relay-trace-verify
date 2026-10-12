"""Real Kubernetes Secret projection with fake credentials and no model transport."""

import argparse
import hashlib
import json
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from rtv.benchmark.execution import prepare_pair
from rtv.benchmark.protocol import load
from rtv.cluster.cli import fixture_spec
from rtv.cluster.config import ClusterConfig, pool_label
from rtv.cluster.credentials import authorize, install_secret, remove_secret
from rtv.cluster.kubernetes import Kubernetes, job_manifest
from rtv.mini_agent import API_ENVELOPES, api_envelope
from rtv.runtime.config import Settings
from rtv.runtime.store import spec_hash


def main(args):
    config = ClusterConfig(**json.loads(args.config.read_text()))
    if config.context != "k3d-rtv-m4" or config.namespace != "rtv-m4" or config.model_access:
        raise ValueError("Only the owned, uncredentialed local pool is supported")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    settings = Settings(output, config.control_database_url, "", schema="rtv4")
    spec = fixture_spec(
        adapter="codex",
        task_contract=args.contract,
        scripted_controls=None,
        allow_model_calls=True,
        agent_seconds=300,
        ttl=1800,
        staged=True,
        pause_after_checkpoint=True,
    )
    spec["api_envelope"] = args.api_envelope
    run = prepare_pair(load(), spec, output, mode="real-model")
    kube = Kubernetes(config)
    refs, allocations, checks = {}, [], {}
    access = None
    started = time.monotonic()
    error = None
    with TemporaryDirectory(prefix="rtv-sentinel-") as private:
        private = Path(private)
        values = {
            "codex": json.dumps({"tokens": {"access_token": "rtv-fake-codex-do-not-use"}}),
            "mini": "rtv-fake-api-key-do-not-use",
        }
        try:
            for adapter, value in values.items():
                path = private / adapter
                path.write_text(value)
                path.chmod(0o600)
                refs[adapter] = install_secret(kube, run["run_sha256"], adapter, path)
            access = authorize(run, round(32 * api_envelope(spec).reservation_usd, 9), refs)
            config = replace(config, model_access=access)
            kube.config = config
            for number, adapter in enumerate(("codex", "mini")):
                entry = next(
                    e for e in [run["source"], *run["entries"]] if e["spec"]["adapter"] == adapter
                )
                row = {
                    **entry,
                    "spec_hash": spec_hash(entry["spec"]),
                    "epoch": 1,
                    "execution_id": f"probe-{adapter}-{run['run_sha256'][:12]}",
                    "deadline": datetime.now(UTC) + timedelta(seconds=180),
                }
                allocation = {
                    "job_name": row["execution_id"],
                    "job_uid": None,
                    "execution_id": row["execution_id"],
                }
                allocations.append(allocation)
                manifest = job_manifest(settings, config, row, allocation)
                pod = manifest["spec"]["template"]["spec"]
                pod["nodeSelector"]["kubernetes.io/hostname"] = f"k3d-rtv-m4-agent-{number}"
                # Replace the production entrypoint: no supervisor, agent or client is run.
                probe_row = json.dumps({k: row[k] for k in ("spec", "trial_id", "epoch")})
                checksum = hashlib.sha256(values[adapter].encode()).hexdigest()
                expected_env = (
                    ["RTV_ALLOW_MODEL_CALLS", "RTV_CODEX_AUTH_FILE"]
                    if adapter == "codex"
                    else ["RTV_ALLOW_API_CALLS", "RTV_API_KEY_FILE"]
                )
                code = "\n".join(
                    [
                        "import hashlib,json,os,stat,sys",
                        "from pathlib import Path",
                        "from rtv.cluster.credentials import supervisor_environment,ENV_KEYS",
                        "pool=json.loads(os.environ['RTV_CLUSTER_POOL'])",
                        f"row=json.loads({probe_row!r})",
                        "path=Path('/run/rtv-model/credential')",
                        f"assert hashlib.sha256(path.read_bytes()).hexdigest()=={checksum!r}",
                        "assert stat.S_IMODE(path.stat().st_mode)==0o400",
                        "for k in ENV_KEYS: os.environ[k]='inherited-fake-value'",
                        "env=supervisor_environment(pool,os.environ['RTV_RUNTIME_ROOT'],row)",
                        f"expected={expected_env!r}",
                        "assert ENV_KEYS.intersection(env)==set(expected)",
                        "assert all('inherited-fake-value'!=v for v in env.values())",
                        "token=Path('/var/run/secrets/kubernetes.io/serviceaccount/token')",
                        "assert not token.exists()",
                        "assert not any(k in sys.modules for k in ('openai','minisweagent'))",
                        "print(json.dumps({'projection':True,'mode_0400':True,'adapter_only':True,'no_serviceaccount':True,'no_model_client':True}))",
                    ]
                )
                executor = pod["containers"][0]
                executor.update(command=["python", "-c", code], args=[])
                # Projection is the subject of this test; no daemon or task filesystem used.
                executor["securityContext"] = {"runAsUser": 0, "allowPrivilegeEscalation": False}
                created = kube.ensure(manifest)
                allocation["job_uid"] = created["metadata"]["uid"]
                end = time.monotonic() + 180
                while time.monotonic() < end:
                    state = kube.job(allocation["job_name"])
                    conditions = state.get("status", {}).get("conditions", [])
                    if any(
                        c["status"] == "True" and c["type"] in {"Complete", "Failed"}
                        for c in conditions
                    ):
                        break
                    time.sleep(1)
                pods = kube.pods(allocation)
                if len(pods) != 1:
                    raise AssertionError("Probe must have exactly one Pod")
                name = pods[0]["metadata"]["name"]
                response = kube.core.read_namespaced_pod_log(
                    name, config.namespace, _request_timeout=(3, 10), _preload_content=False
                )
                log = response.data.decode()
                if any(value in log for value in values.values()):
                    raise AssertionError("Probe log contains a sentinel credential")
                (output / f"{adapter}-pod.log").write_text(log)
                checks[adapter + "_pod_succeeded"] = pods[0]["status"]["phase"] == "Succeeded"
                if not checks[adapter + "_pod_succeeded"]:
                    raise AssertionError("Projection probe failed")
                checks.update({adapter + "_" + k: v for k, v in json.loads(log).items()})
                checks[adapter + "_assigned_node"] = (
                    pods[0]["spec"]["nodeName"] == f"k3d-rtv-m4-agent-{number}"
                )
                end = time.monotonic() + 60
                while not kube.remove(allocation, pool_label(settings)):
                    if time.monotonic() >= end:
                        raise TimeoutError("Probe cleanup timed out")
                    time.sleep(1)
        except Exception as exc:
            error = type(exc).__name__
        finally:
            for allocation in allocations:
                end = time.monotonic() + 60
                while not kube.remove(allocation, pool_label(settings)):
                    if time.monotonic() >= end:
                        raise TimeoutError("Probe cleanup timed out")
                    time.sleep(1)
            for ref in refs.values():
                remove_secret(kube, {"run_sha256": run["run_sha256"]}, ref)
            checks["jobs_and_pods_removed"] = all(
                kube.job(a["job_name"]) is None and not kube.pods(a) for a in allocations
            )
            remaining = kube.core.list_namespaced_secret(
                config.namespace,
                label_selector="rtv.dev/credential-run=" + run["run_sha256"][:63],
                _request_timeout=(3, 10),
            ).items
            checks["sentinel_secrets_removed"] = not remaining
            checks["no_sentinel_in_artifacts"] = not any(
                value.encode() in p.read_bytes()
                for value in values.values()
                for p in output.rglob("*")
                if p.is_file()
            )
            evidence = {
                "status": "passed" if not error and all(checks.values()) else "failed",
                "mode": "real-local-kubernetes-fake-credentials",
                "checks": checks,
                "error_type": error,
                "model_calls": 0,
                "api_calls": 0,
                "cloud_cost_usd": 0,
                "runner_image": config.runner_image,
                "run_sha256": run["run_sha256"],
                "elapsed_seconds": round(time.monotonic() - started, 2),
                "not_tested": [
                    "real authentication",
                    "model execution",
                    "inner Codex auth copy",
                    "Codex native repository session",
                    "untrusted tenant isolation",
                ],
            }
            (output / "report.json").write_text(json.dumps(evidence, indent=2) + "\n")
            kube.close()
            print(json.dumps(evidence, indent=2))
    return 0 if evidence["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("config", "contract", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--api-envelope", choices=API_ENVELOPES, default="mini-40kb-v1")
    raise SystemExit(main(parser.parse_args()))
