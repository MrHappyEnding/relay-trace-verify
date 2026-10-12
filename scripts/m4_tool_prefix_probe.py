"""Real pinned CLI + Docker, scripted loopback model responses, zero provider calls.

No auth file is read/mounted. Prefix and native continuation use separate offline
containers with a host-verified checkpoint restore between them.
"""

import argparse
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

from rtv import codex_gate
from rtv.codex_agent import CODEX_VERSION, read_turn
from rtv.domain import ExecutionRef
from rtv.harbor_probe import tree_digest
from rtv.storage import LocalCheckpointStore


def write(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def inside(output, stage):
    home = Path("/tmp/rtv-offline-codex")
    home.mkdir()
    logs = output / "agent"
    logs.mkdir()
    workspace = Path("/testbed")
    native = None
    if stage == "resume":
        prior = output / "restored"
        # This is the fresh disposable image's base tree, never a host checkout.
        # Replace it so read-only Git objects need not be overwritten with dropped caps.
        shutil.rmtree(workspace)
        shutil.copytree(prior / "workspace", workspace, symlinks=True)
        native = next((prior / "native").glob("*.jsonl"))
        sessions = home / "sessions/2026/10/12"
        sessions.mkdir(parents=True)
        shutil.copy2(native, sessions / native.name)
    gate = logs / "tool-gate.json"
    if stage == "prefix":
        write(gate, codex_gate.initial({"version": codex_gate.VERSION, "max_tools": 2}))
    calls = (
        [
            'let p=await tools.exec_command({"cmd":"sleep 2; printf prefix > rtv-prefix.txt",'
            '"yield_time_ms":1000}); while(p.session_id){p=await tools.write_stdin('
            '{session_id:p.session_id,chars:"",yield_time_ms:1000});} text(p);',
            "text(await tools.apply_patch("
            + json.dumps(
                "*** Begin Patch\n*** Add File: rtv-patch.txt\n+patch saved\n*** End Patch\n"
            )
            + "));",
            'text(await tools.exec_command({"cmd":"printf forbidden > rtv-forbidden.txt"}));',
        ]
        if stage == "prefix"
        else [
            'text(await tools.exec_command({"cmd":"test $(cat rtv-prefix.txt) = prefix '
            '&& test -f rtv-patch.txt && printf resumed > rtv-resumed.txt"}));'
        ]
    )
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            if self.path != "/v1/responses" or len(requests) > len(calls):
                self.send_error(400)
                return
            # Consume the request without persisting model input or environment metadata.
            self.rfile.read(int(self.headers["Content-Length"]))
            n = len(requests)
            requests.append(n)
            item = (
                {
                    "type": "custom_tool_call",
                    "id": f"tool_{n}",
                    "call_id": f"call_{n}",
                    "name": "exec",
                    "input": calls[n],
                }
                if n < len(calls)
                else {
                    "type": "message",
                    "id": f"msg_{n}",
                    "role": "assistant",
                    "status": "completed",
                    "content": [
                        {
                            "type": "output_text",
                            "text": "Saved prefix; remaining work needs continuation.",
                        }
                    ],
                }
            )
            response = {
                "id": f"response_{n}",
                "object": "response",
                "status": "completed",
                "output": [item],
                "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
            }
            events = [
                {
                    "type": "response.created",
                    "response": {**response, "status": "in_progress", "output": []},
                },
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {"type": "response.completed", "response": response},
            ]
            body = "".join(
                "event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    config = (
        'model="gpt-6-astra"\nmodel_provider="fixture"\nweb_search="disabled"\n'
        '[model_providers.fixture]\nname="offline scripted fixture"\n'
        f'base_url="http://127.0.0.1:{server.server_port}/v1"\n'
        'wire_api="responses"\nrequires_openai_auth=false\n'
        "[features]\nmulti_agent=false\n"
    )
    if stage == "prefix":
        hooks = codex_gate.hook_config(str(Path(codex_gate.__file__)), str(gate))
        for name, entries in hooks.items():
            command = entries[0]["hooks"][0]["command"]
            config += (
                f'[[hooks.{name}]]\nmatcher="*"\n[[hooks.{name}.hooks]]\n'
                f'type="command"\ncommand={json.dumps(command)}\n'
            )
    (home / "config.toml").write_text(config)
    command = ["codex", "exec"] + (["resume", "--last"] if native else [])
    command += [
        "--dangerously-bypass-approvals-and-sandbox",
        "--skip-git-repo-check",
        "--enable",
        "unified_exec",
        "--json",
    ]
    if stage == "prefix":
        command.append("--dangerously-bypass-hook-trust")
    command += ["--", "Offline scripted transport test; no model inference."]
    try:
        before = codex_gate.process_snapshot()
        result = subprocess.run(
            command,
            env={**os.environ, "CODEX_HOME": str(home)},
            cwd=workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=90,
        )
        codex_gate.check_processes(before, codex_gate.process_snapshot())
    finally:
        server.shutdown()
    (logs / "codex.txt").write_text(result.stdout)
    (logs / "stderr.txt").write_text(result.stderr)
    if result.returncode:
        raise RuntimeError("Offline CLI failed; inspect local logs")
    shutil.copytree(home / "sessions", logs / "sessions")
    turn = read_turn(logs, native=native, workspace="/testbed")
    report = {"session_id": turn.session_id, "fixture_responses": len(requests)}
    if stage == "prefix":
        report["boundary"] = codex_gate.audit(
            gate, turn, {"version": codex_gate.VERSION, "max_tools": 2}
        )
        assert (workspace / "rtv-prefix.txt").read_text() == "prefix"
        assert (workspace / "rtv-patch.txt").read_text() == "patch saved\n"
        assert not (workspace / "rtv-forbidden.txt").exists()
        bundle = output / "bundle"
        (bundle / "native").mkdir(parents=True)
        shutil.copytree(workspace, bundle / "workspace", symlinks=True)
        shutil.copy2(turn.rollout, bundle / "native" / turn.rollout.name)
        shutil.copy2(gate, bundle / "tool-gate.json")
    else:
        assert (workspace / "rtv-resumed.txt").read_text() == "resumed"
        assert not gate.exists()
    write(output / "result.json", report)


def main(args):
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    image = json.loads(
        subprocess.check_output(["docker", "image", "inspect", args.image], text=True)
    )[0]
    if image["Architecture"] != "amd64":
        raise ValueError("Probe requires the pinned AMD64 agent image")
    repo = Path(__file__).resolve().parents[1]
    containers = []

    def run(stage):
        destination = output / stage
        destination.mkdir(exist_ok=True)
        cid = subprocess.check_output(
            [
                "docker",
                "create",
                "--network",
                "none",
                "--platform",
                "linux/amd64",
                "--cpus",
                "1",
                "--memory",
                "2g",
                "--pids-limit",
                "512",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "-v",
                f"{repo / 'src'}:/rtv-src:ro",
                "-v",
                f"{Path(__file__).resolve()}:/probe.py:ro",
                "-v",
                f"{destination}:/output:rw",
                "-e",
                "PYTHONPATH=/rtv-src",
                "--entrypoint",
                "python",
                image["Id"],
                "/probe.py",
                "--inside",
                stage,
                "--output",
                "/output",
            ],
            text=True,
        ).strip()
        containers.append(cid)
        with (destination / "container.log").open("w") as stream:
            subprocess.run(
                ["docker", "start", "-a", cid],
                check=True,
                stdout=stream,
                stderr=stream,
                timeout=150,
            )
        state = json.loads(
            subprocess.check_output(
                ["docker", "inspect", "--format", "{{json .State}}", cid], text=True
            )
        )
        if state["Running"] or state["Pid"] or state["ExitCode"]:
            raise RuntimeError("Probe container did not exit cleanly; inspect its log")
        return json.loads((destination / "result.json").read_text())

    report = {
        "mode": "real-cli-container-scripted-model",
        "paid_api_calls": 0,
        "real_model_calls": 0,
        "image_id": image["Id"],
        "cli_version": CODEX_VERSION,
        "network": "none",
        "checks": {},
        "status": "failed",
    }
    try:
        prefix = run("prefix")
        report["prefix"] = prefix
        storage = LocalCheckpointStore(output / "checkpoints")
        ref = storage.commit(
            output / "prefix/bundle",
            ExecutionRef(uuid4()),
            event_cursor=0,
            summary="offline scripted tool-prefix probe",
            context={"boundary": prefix["boundary"]},
            preserve_directory_modes=True,
        )
        destination = output / "resume/restored"
        storage.restore(ref, destination)
        report["checks"]["workspace_restored"] = tree_digest(destination / "workspace") == (
            tree_digest(output / "prefix/bundle/workspace")
        )
        resumed = run("resume")
        report["checks"].update(
            native_session_preserved=resumed["session_id"] == prefix["session_id"],
            exact_tool_allowance=prefix["boundary"]["completed_tools"] == 2,
            excess_tool_denied=prefix["boundary"]["denied_tools"] == 1,
            fresh_container_continuation=True,
            no_surviving_agent_processes=True,
        )
        report["status"] = "passed" if all(report["checks"].values()) else "failed"
    finally:
        for cid in containers:
            subprocess.run(["docker", "rm", "-f", cid], check=True, capture_output=True)
        report["containers_removed"] = len(containers)
        write(output / "report.json", report)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--image")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--inside", choices=("prefix", "resume"))
    args = parser.parse_args()
    if args.inside:
        inside(args.output, args.inside)
    else:
        if not args.image:
            parser.error("--image required")
        raise SystemExit(main(args))
