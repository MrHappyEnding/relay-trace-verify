"""Read-only loopback views with separate incremental and prefix accounting."""

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from rtv.runtime.relay_store import RelayStore


def experiment_view(settings, eid):
    store = RelayStore(settings)
    experiment = store.experiment(eid)
    trials = []
    for row in experiment.pop("trials"):
        handoffs = []
        with store.connect() as c:
            handoffs = c.execute(
                "SELECT handoff_id,policy,state,prepared,error,source_attempt,"
                "target_attempt,checkpoint_id "
                "FROM handoffs WHERE target_trial=%s ORDER BY created_at",
                (row["trial_id"],),
            ).fetchall()
        trials.append(
            {
                "trial_id": row["trial_id"],
                "label": row["branch_label"],
                "status": row["status"],
                "epoch": row["epoch"],
                "attempt_id": row["attempt_id"],
                "adapter": row["spec"]["adapter"],
                "model": row["spec"]["model"],
                "checkpoint": row["latest_checkpoint"],
                "result": row["result"],
                "cleanup": row["cleanup"],
                "ledger": store.ledger(row["trial_id"]),
                "handoffs": handoffs,
                "events": store.events(row["trial_id"]),
            }
        )
    return {
        **experiment,
        "trials": trials,
        "interpretation": (
            "One development task; no handoff performance conclusion. "
            "Prefix is counted once per policy, not once per physical branch execution."
        ),
    }


def serve(settings, port=8765):
    if not 1024 <= port <= 65535:
        raise ValueError("Choose a loopback port between 1024 and 65535")
    html = Path(__file__).with_name("web") / "index.html"

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlparse(self.path).path
            try:
                if path == "/":
                    body = html.read_bytes()
                    mime = "text/html; charset=utf-8"
                elif path == "/api/experiments":
                    body = json.dumps(RelayStore(settings).experiments(), default=str).encode()
                    mime = "application/json"
                elif path.startswith("/api/experiment/"):
                    eid = str(UUID(path.rsplit("/", 1)[1]))
                    body = json.dumps(experiment_view(settings, eid), default=str).encode()
                    mime = "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except ValueError:
                self.send_error(404)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"RTV viewer: http://127.0.0.1:{port}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()
