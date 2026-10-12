"""Durable resource reservations. Lease loss never releases physical capacity."""

from contextlib import contextmanager

from psycopg.types.json import Jsonb

from rtv.cluster.config import pool_label
from rtv.runtime.config import TERMINAL
from rtv.runtime.store import Fenced, Store

SCHEMA = """
CREATE TABLE IF NOT EXISTS cluster_pools (
 root TEXT PRIMARY KEY, config JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS cluster_allocations (
 execution_id TEXT PRIMARY KEY REFERENCES executions(execution_id),
 root TEXT NOT NULL REFERENCES cluster_pools(root), trial_id TEXT NOT NULL,
 job_name TEXT UNIQUE NOT NULL, job_uid TEXT, node_name TEXT,
 avoid_nodes JSONB NOT NULL DEFAULT '[]',
 state TEXT NOT NULL DEFAULT 'reserved', cpu_millis INT NOT NULL, memory_mb INT NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), released_at TIMESTAMPTZ
);
"""


class ClusterStore(Store):
    def initialize_pool(self, config):
        config.validate_root(self.settings)
        self.initialize()
        with self.connect() as c:
            c.execute("SELECT pg_advisory_xact_lock(72004001)")
            c.execute(SCHEMA)
            c.execute(
                "ALTER TABLE cluster_allocations ADD COLUMN IF NOT EXISTS "
                "avoid_nodes JSONB NOT NULL DEFAULT '[]'"
            )
            c.execute(
                "INSERT INTO cluster_pools VALUES(%s,%s) ON CONFLICT DO NOTHING",
                (str(self.settings.root), Jsonb(config.frozen())),
            )
            saved = c.execute(
                "SELECT config FROM cluster_pools WHERE root=%s", (str(self.settings.root),)
            ).fetchone()["config"]
            if saved != config.frozen():
                raise ValueError("Frozen pool configuration changed; use a new runtime root")

    def verify_pool(self, config):
        with self.connect() as c:
            saved = c.execute(
                "SELECT config FROM cluster_pools WHERE root=%s", (str(self.settings.root),)
            ).fetchone()
            if not saved or saved["config"] != config.frozen():
                raise ValueError("Frozen pool configuration changed; use a new runtime root")

    @contextmanager
    def reconcile_lock(self, trial_id):
        # A server-owned session lock, not a host filesystem lock. Connection loss
        # releases it; idempotent Job names and DB fences cover uncertain API calls.
        with self.connect() as c:
            acquired = c.execute(
                "SELECT pg_try_advisory_lock(hashtext(%s),72004) AS ok", (trial_id,)
            ).fetchone()["ok"]
            try:
                yield acquired
            finally:
                if acquired:
                    c.execute("SELECT pg_advisory_unlock(hashtext(%s),72004)", (trial_id,))

    def reserve(self, expected):
        with self.connect() as c:
            config = c.execute(
                "SELECT config FROM cluster_pools WHERE root=%s FOR UPDATE",
                (str(self.settings.root),),
            ).fetchone()["config"]
            row = self._row(c, expected["trial_id"], True)
            if row["execution_id"] != expected["execution_id"] or row["cancel_requested"]:
                raise Fenced("Allocation no longer owns this execution")
            if row["status"] != "queued" or row["execution_state"] != "queued":
                return None
            if c.execute(
                "SELECT deadline<=clock_timestamp() AS expired FROM trials WHERE trial_id=%s",
                (row["trial_id"],),
            ).fetchone()["expired"]:
                return None
            prior = c.execute(
                "SELECT * FROM cluster_allocations WHERE execution_id=%s",
                (row["execution_id"],),
            ).fetchone()
            if prior:
                if prior["state"] == "released":
                    raise Fenced("A released allocation cannot launch again")
                return prior
            usage = c.execute(
                "SELECT count(*) AS jobs,COALESCE(sum(cpu_millis),0) AS cpu,"
                "COALESCE(sum(memory_mb),0) AS memory FROM cluster_allocations "
                "WHERE root=%s AND state<>'released'",
                (str(self.settings.root),),
            ).fetchone()
            if (
                usage["jobs"] >= config["max_jobs"]
                or usage["cpu"] + config["cpu_millis"] > config["max_jobs"] * config["cpu_millis"]
                or usage["memory"] + config["memory_mb"] > config["max_jobs"] * config["memory_mb"]
            ):
                return None
            # Freeze the previous physical node into the new epoch's contract.
            # Its kubelet may still look Ready briefly after heartbeat loss.
            avoid_nodes = []
            if row["recoveries"]:
                previous = c.execute(
                    "SELECT node_name FROM cluster_allocations WHERE trial_id=%s "
                    "AND node_name IS NOT NULL ORDER BY created_at DESC LIMIT 1",
                    (row["trial_id"],),
                ).fetchone()
                if previous:
                    avoid_nodes = [previous["node_name"]]
            allocation = c.execute(
                "INSERT INTO cluster_allocations(execution_id,root,trial_id,job_name,"
                "cpu_millis,memory_mb,avoid_nodes) VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING *",
                (
                    row["execution_id"],
                    str(self.settings.root),
                    row["trial_id"],
                    "rtv-" + row["execution_id"],
                    config["cpu_millis"],
                    config["memory_mb"],
                    Jsonb(avoid_nodes),
                ),
            ).fetchone()
            self._event(
                c,
                row,
                "cluster.reserved",
                {"pool": pool_label(self.settings), "job_name": allocation["job_name"]},
            )
            return allocation

    def allocation(self, execution_id):
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM cluster_allocations WHERE execution_id=%s AND root=%s",
                (execution_id, str(self.settings.root)),
            ).fetchone()

    def observed(self, row, job, pods):
        uid = job["metadata"]["uid"]
        node = next((p["spec"].get("nodeName") for p in pods if p["spec"].get("nodeName")), None)
        with self.connect() as c:
            allocation = c.execute(
                "SELECT * FROM cluster_allocations WHERE execution_id=%s FOR UPDATE",
                (row["execution_id"],),
            ).fetchone()
            if allocation["job_uid"] not in {None, uid}:
                raise Fenced("Job UID changed; refuse to adopt a replacement")
            c.execute(
                "UPDATE cluster_allocations SET job_uid=%s,node_name=COALESCE(%s,node_name),"
                "state=CASE WHEN state='released' THEN state ELSE 'active' END "
                "WHERE execution_id=%s",
                (uid, node, row["execution_id"]),
            )
            self._event(c, row, "cluster.job_observed", {"job_uid": uid})
            if node:
                self._event(c, row, "cluster.scheduled", {"node": node, "job_uid": uid})

    def release(self, row):
        """Caller must observe both the exact Job UID and its Pods gone first."""
        with self.connect() as c:
            c.execute(
                "UPDATE cluster_allocations SET state='released',released_at=clock_timestamp() "
                "WHERE execution_id=%s AND root=%s AND state<>'released'",
                (row["execution_id"], str(self.settings.root)),
            )
            c.execute(
                "UPDATE executions SET cleanup='complete' WHERE execution_id=%s",
                (row["execution_id"],),
            )
            c.execute(
                "UPDATE trials SET cleanup='complete' WHERE execution_id=%s AND status=ANY(%s)",
                (row["execution_id"], list(TERMINAL | {"sealed", "checkpointed"})),
            )
            self._event(c, row, "cluster.released", {})

    def pool_status(self):
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM cluster_allocations WHERE root=%s ORDER BY created_at",
                (str(self.settings.root),),
            ).fetchall()
