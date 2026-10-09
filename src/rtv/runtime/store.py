"""Version 3 state: epoch-fenced writes, immutable checkpoints, cumulative usage."""

import hashlib
import json
from contextlib import contextmanager
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from rtv.runtime.config import LEASE_SECONDS, TERMINAL, Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS trials (
 trial_id TEXT PRIMARY KEY, operation_id TEXT UNIQUE NOT NULL, attempt_id TEXT UNIQUE NOT NULL,
 execution_id TEXT UNIQUE NOT NULL, epoch INT NOT NULL CHECK(epoch>0), root TEXT NOT NULL,
 spec JSONB NOT NULL, spec_hash TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued',
 cancel_requested BOOL NOT NULL DEFAULT FALSE, cleanup TEXT NOT NULL DEFAULT 'pending',
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), deadline TIMESTAMPTZ NOT NULL,
 result JSONB, latest_checkpoint TEXT, recoveries INT NOT NULL DEFAULT 0,
 ledger_revision BIGINT NOT NULL DEFAULT 0, parent_checkpoint TEXT,
 experiment_id TEXT, branch_label TEXT, prefix_operations JSONB NOT NULL DEFAULT '[]'
);
ALTER TABLE trials ADD COLUMN IF NOT EXISTS artifacts_retired BOOL NOT NULL DEFAULT FALSE;
CREATE TABLE IF NOT EXISTS attempts (
 attempt_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(trial_id),
 spec JSONB NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS executions (
 execution_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(trial_id),
 attempt_id TEXT NOT NULL REFERENCES attempts(attempt_id), spec JSONB NOT NULL,
 handoff_id TEXT, epoch INT NOT NULL, stage INT NOT NULL, resume_from TEXT,
 state TEXT NOT NULL DEFAULT 'queued',
 cleanup TEXT NOT NULL DEFAULT 'pending', lease_until TIMESTAMPTZ, result JSONB,
 operation_id TEXT UNIQUE NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(trial_id,epoch)
);
CREATE TABLE IF NOT EXISTS events (
 sequence BIGSERIAL PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(trial_id),
 event_id TEXT UNIQUE NOT NULL, kind TEXT NOT NULL, payload JSONB NOT NULL,
 at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS operations (
 operation_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(trial_id),
 execution_id TEXT NOT NULL REFERENCES executions(execution_id), kind TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending', usage JSONB, reason TEXT, cost_usd DOUBLE PRECISION,
 parent_operation TEXT REFERENCES operations(operation_id), reserved_usd DOUBLE PRECISION,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS checkpoints (
 checkpoint_id TEXT PRIMARY KEY, trial_id TEXT NOT NULL REFERENCES trials(trial_id),
 execution_id TEXT NOT NULL REFERENCES executions(execution_id), epoch INT NOT NULL,
 stage INT NOT NULL, state TEXT NOT NULL DEFAULT 'preparing', manifest_sha256 TEXT,
 context JSONB, parent TEXT REFERENCES checkpoints(checkpoint_id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), committed_at TIMESTAMPTZ,
 UNIQUE(execution_id,stage)
);
CREATE TABLE IF NOT EXISTS checkpoint_refs (
 checkpoint_id TEXT NOT NULL REFERENCES checkpoints(checkpoint_id),
 owner TEXT NOT NULL, PRIMARY KEY(checkpoint_id,owner)
);
CREATE TABLE IF NOT EXISTS experiments (
 experiment_id TEXT PRIMARY KEY, root TEXT NOT NULL, request_hash TEXT NOT NULL,
 source_checkpoint TEXT NOT NULL REFERENCES checkpoints(checkpoint_id), config JSONB NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE IF NOT EXISTS handoffs (
 handoff_id TEXT PRIMARY KEY, root TEXT NOT NULL, request_hash TEXT NOT NULL,
 kind TEXT NOT NULL, source_trial TEXT NOT NULL REFERENCES trials(trial_id),
 source_execution TEXT NOT NULL, source_attempt TEXT NOT NULL, source_epoch INT NOT NULL,
 checkpoint_id TEXT NOT NULL REFERENCES checkpoints(checkpoint_id),
 target_trial TEXT NOT NULL REFERENCES trials(trial_id), target_attempt TEXT NOT NULL,
 target_execution TEXT NOT NULL, target_spec JSONB NOT NULL, policy TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'requested', prepared JSONB, error TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(), completed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS one_pending_handoff ON handoffs(target_trial)
 WHERE state IN ('requested','prepared');
        """


class Fenced(ValueError):
    """Caller has lost its epoch or lease."""


def identity(root, key):
    if not key.strip() or len(key) > 200:
        raise ValueError("Idempotency key requires 1–200 characters")
    operation = str(uuid5(NAMESPACE_URL, f"rtv:m3:{root}:{key}"))
    return {
        "operation_id": operation,
        **{
            k: str(uuid5(NAMESPACE_URL, operation + ":" + k))
            for k in ("trial_id", "attempt_id", "execution_id")
        },
    }


def spec_hash(spec):
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self):
        return psycopg.connect(
            self.settings.dsn,
            row_factory=dict_row,
            connect_timeout=5,
            options="-c statement_timeout=10000 -c search_path=rtv3",
        )

    def initialize(self):
        with self.connect() as c:
            c.execute("SELECT pg_advisory_xact_lock(72003001)")
            c.execute("CREATE SCHEMA IF NOT EXISTS rtv3")
            c.execute(SCHEMA)

    def _row(self, c, trial_id, locking=False):
        if locking:
            # Lock the stable Trial first, then resolve its current execution in
            # a fresh READ COMMITTED statement. A locking join can re-evaluate
            # against the superseded execution and incorrectly return no row.
            c.execute(
                "SELECT trial_id FROM trials WHERE trial_id=%s AND root=%s FOR UPDATE",
                (trial_id, str(self.settings.root)),
            )
        row = c.execute(
            "SELECT t.*,e.stage,e.resume_from,e.state AS execution_state,e.lease_until,"
            "e.handoff_id,e.cleanup AS execution_cleanup,(e.lease_until>clock_timestamp())"
            " AS lease_valid "
            "FROM trials t JOIN executions e ON e.execution_id=t.execution_id "
            "WHERE t.trial_id=%s AND t.root=%s" + (" FOR UPDATE OF e" if locking else ""),
            (trial_id, str(self.settings.root)),
        ).fetchone()
        if not row:
            raise ValueError("Unknown trial in this runtime root")
        return row

    @contextmanager
    def owned(self, expected, *, live=True, allow_cancel=False):
        with self.connect() as c:
            row = self._row(c, expected["trial_id"], True)
            if any(row[k] != expected[k] for k in ("execution_id", "epoch", "attempt_id")):
                raise Fenced("Stale execution token")
            if live:
                valid = c.execute(
                    "SELECT lease_until>clock_timestamp() AS ok FROM executions "
                    "WHERE execution_id=%s",
                    (row["execution_id"],),
                ).fetchone()["ok"]
                if row["execution_state"] != "running" or not valid:
                    raise Fenced("Execution lease expired or is not active")
                if row["cancel_requested"] and not allow_cancel:
                    raise Fenced("Cancelled trial cannot admit work or publish checkpoints")
                if (
                    not allow_cancel
                    and c.execute(
                        "SELECT deadline<=clock_timestamp() AS expired FROM trials "
                        "WHERE trial_id=%s",
                        (row["trial_id"],),
                    ).fetchone()["expired"]
                ):
                    raise Fenced("Trial deadline prohibits new work/publication")
            yield c, row

    @staticmethod
    def _event(c, row, kind, payload):
        payload = {
            **payload,
            **{k: row[k] for k in ("operation_id", "attempt_id", "execution_id", "epoch")},
        }
        c.execute(
            "INSERT INTO events(trial_id,event_id,kind,payload) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT(event_id) DO NOTHING",
            (row["trial_id"], row["execution_id"] + ":" + kind, kind, Jsonb(payload)),
        )

    def submit(self, key, spec):
        ids = identity(str(self.settings.root), key)
        with self.connect() as c:
            c.execute(
                "INSERT INTO trials(trial_id,operation_id,attempt_id,execution_id,epoch,root,"
                "spec,spec_hash,deadline) VALUES(%s,%s,%s,%s,1,%s,%s,%s,clock_timestamp()+"
                "(%s * interval '1 second')) ON CONFLICT DO NOTHING",
                (
                    *[ids[k] for k in ("trial_id", "operation_id", "attempt_id", "execution_id")],
                    str(self.settings.root),
                    Jsonb(spec),
                    spec_hash(spec),
                    spec["ttl_seconds"],
                ),
            )
            c.execute(
                "INSERT INTO attempts(attempt_id,trial_id,spec) VALUES(%s,%s,%s) ON"
                " CONFLICT DO NOTHING",
                (ids["attempt_id"], ids["trial_id"], Jsonb(spec)),
            )
            c.execute(
                "INSERT INTO"
                " executions(execution_id,trial_id,attempt_id,spec,epoch,stage,operation_id) "
                "VALUES(%s,%s,%s,%s,1,1,%s) ON CONFLICT DO NOTHING",
                (
                    ids["execution_id"],
                    ids["trial_id"],
                    ids["attempt_id"],
                    Jsonb(spec),
                    ids["operation_id"],
                ),
            )
            row = self._row(c, ids["trial_id"], True)
            if row["spec_hash"] != spec_hash(spec):
                raise ValueError("Idempotency key already belongs to a different request")
            self._event(c, row, "trial.submitted", {"spec_hash": row["spec_hash"]})
            return row

    def get(self, trial_id):
        with self.connect() as c:
            return self._row(c, trial_id)

    def claim(self, expected):
        with self.owned(expected, live=False) as (c, row):
            if (
                row["execution_state"] != "queued"
                or row["status"] != "queued"
                or row["cancel_requested"]
            ):
                return False
            c.execute(
                "UPDATE executions SET state='running',lease_until=clock_timestamp()+"
                "(%s * interval '1 second') WHERE execution_id=%s",
                (LEASE_SECONDS, row["execution_id"]),
            )
            c.execute("UPDATE trials SET status='running' WHERE trial_id=%s", (row["trial_id"],))
            self._event(c, row, "execution.claimed", {})
            return True

    def heartbeat(self, expected):
        with self.owned(expected, allow_cancel=True) as (c, row):
            c.execute(
                "UPDATE executions SET lease_until=clock_timestamp()+"
                "(%s * interval '1 second') WHERE execution_id=%s",
                (LEASE_SECONDS, row["execution_id"]),
            )
            return row

    def event(self, expected, kind, payload):
        with self.owned(expected) as (c, row):
            self._event(c, row, kind, payload)

    def phase(self, expected, phase, payload):
        if phase not in {"running", "verifying"}:
            raise ValueError("Invalid phase")
        with self.owned(expected) as (c, row):
            if row["status"] not in (
                {"running"} if phase == "running" else {"running", "verifying"}
            ):
                raise ValueError("Execution phase cannot move backwards")
            c.execute("UPDATE trials SET status=%s WHERE trial_id=%s", (phase, row["trial_id"]))
            self._event(c, row, "execution." + phase, payload)

    def cancel(self, trial_id):
        with self.connect() as c:
            row = self._row(c, trial_id, True)
            if row["status"] not in TERMINAL:
                c.execute(
                    "UPDATE handoffs SET state='aborted',error='cancelled' "
                    "WHERE target_trial=%s AND state IN ('requested','prepared')",
                    (trial_id,),
                )
                c.execute(
                    "UPDATE trials SET cancel_requested=TRUE,status='cancel_requested' "
                    "WHERE trial_id=%s",
                    (trial_id,),
                )
                self._event(c, row, "trial.cancel_requested", {})
        return self.get(trial_id)

    def begin_call(self, expected):
        operation = str(uuid5(NAMESPACE_URL, expected["execution_id"] + ":agent-turn"))
        with self.owned(expected) as (c, row):
            inserted = c.execute(
                "INSERT INTO operations(operation_id,trial_id,execution_id,kind) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING operation_id",
                (operation, row["trial_id"], row["execution_id"], row["spec"]["adapter"]),
            ).fetchone()
            if not inserted:
                raise Fenced("Agent invocation already admitted; replay forbidden")
            c.execute(
                "UPDATE trials SET ledger_revision=ledger_revision+1 WHERE trial_id=%s",
                (row["trial_id"],),
            )
            self._event(c, row, "agent.started", {"call_operation_id": operation})
        return operation

    def settle_call(self, expected, operation, usage, *, cost_usd=None, failure=None):
        if usage is not None and (
            not isinstance(usage, dict) or any(type(v) is not int or v < 0 for v in usage.values())
        ):
            raise ValueError("Usage must contain nonnegative integer counters")
        # A late observation settles only its original operation. It cannot publish
        # current state. Known observations are immutable; conflicting retries fail.
        with self.connect() as c:
            current = self._row(c, expected["trial_id"], True)
            op = c.execute(
                "SELECT * FROM operations WHERE operation_id=%s AND execution_id=%s "
                "AND trial_id=%s FOR UPDATE",
                (operation, expected["execution_id"], current["trial_id"]),
            ).fetchone()
            if not op:
                raise ValueError("Unknown operation")
            state = (
                "known"
                if usage is not None or op["kind"] in {"fixture", "mini", "mini-fixture"}
                else "unknown"
            )
            if op["state"] == "known":
                if op["usage"] != usage or op["cost_usd"] != cost_usd:
                    raise ValueError("Conflicting settled usage")
                return
            c.execute(
                "UPDATE operations SET state=%s,usage=%s,cost_usd=%s,"
                "reason=COALESCE(%s,reason) WHERE operation_id=%s",
                (
                    state,
                    Jsonb(usage),
                    cost_usd,
                    json.dumps(failure, sort_keys=True) if failure else None,
                    operation,
                ),
            )
            c.execute(
                "UPDATE trials SET ledger_revision=ledger_revision+1 WHERE trial_id=%s",
                (current["trial_id"],),
            )
            self._event(
                c,
                expected,
                "agent.completed" if not op["parent_operation"] else "model.completed:" + operation,
                {
                    "call_operation_id": operation,
                    "usage": usage,
                    "late_observation": current["execution_id"] != expected["execution_id"],
                    **({"api_error": failure} if failure else {}),
                },
            )

    @staticmethod
    def _unknown(c, row, reason):
        changed = c.execute(
            "UPDATE operations SET state='unknown',reason=%s WHERE execution_id=%s "
            "AND state='pending' RETURNING operation_id",
            (reason, row["execution_id"]),
        ).fetchall()
        if changed:
            c.execute(
                "UPDATE trials SET ledger_revision=ledger_revision+1 WHERE trial_id=%s",
                (row["trial_id"],),
            )

    @staticmethod
    def totals(ops):
        totals = {}
        for op in ops:
            if op["state"] == "known":
                for k, v in (op["usage"] or {}).items():
                    totals[k] = totals.get(k, 0) + v
        metered = [o for o in ops if o["kind"] == "mini-api"]
        unknown = sum(o["state"] == "unknown" for o in ops)
        pending = sum(o["state"] == "pending" for o in ops)
        billed = sum(o["cost_usd"] or 0 for o in metered if o["state"] == "known")
        uncertain_cost = any(o["kind"] == "codex" for o in ops) or unknown or pending
        return {
            "known_usage": totals,
            "unknown_operations": unknown,
            "pending_operations": pending,
            "known_api_cost_usd": billed,
            "cost_usd": None if uncertain_cost else billed,
        }

    def ledger(self, trial_id):
        row = self.get(trial_id)
        with self.connect() as c:
            ops = c.execute(
                "SELECT * FROM operations WHERE trial_id=%s ORDER BY created_at", (trial_id,)
            ).fetchall()
            prefix = c.execute(
                "SELECT * FROM operations WHERE operation_id=ANY(%s) ORDER BY created_at",
                (row["prefix_operations"],),
            ).fetchall()
        return {
            **self.totals(ops),
            "operations": ops,
            "prefix": self.totals(prefix),
            "prefix_operation_ids": row["prefix_operations"],
            "end_to_end": self.totals(prefix + ops),
        }

    def begin_model_call(self, expected, parent, index, reserve):
        if type(index) is not int or index < 1 or not 0 < reserve <= 0.5:
            raise ValueError("Invalid API admission reservation")
        operation = str(uuid5(NAMESPACE_URL, parent + ":api:" + str(index)))
        with self.owned(expected) as (c, row):
            if not c.execute(
                "SELECT 1 FROM operations WHERE operation_id=%s AND execution_id=%s "
                "AND state='pending'",
                (parent, row["execution_id"]),
            ).fetchone():
                raise Fenced("Stage invocation is not active")
            charged = c.execute(
                "SELECT COALESCE(SUM(reserved_usd),0) AS total, COUNT(*) AS n "
                "FROM operations WHERE trial_id=%s AND kind='mini-api'",
                (row["trial_id"],),
            ).fetchone()
            if charged["n"] >= row["spec"].get("api_call_limit", 8) or charged[
                "total"
            ] + reserve > row["spec"].get("api_budget_usd", 0):
                raise ValueError("API admission budget exhausted; no request sent")
            if not c.execute(
                "INSERT INTO"
                " operations(operation_id,trial_id,execution_id,kind,"
                "parent_operation,reserved_usd) "
                "VALUES(%s,%s,%s,'mini-api',%s,%s) ON CONFLICT DO NOTHING RETURNING operation_id",
                (operation, row["trial_id"], row["execution_id"], parent, reserve),
            ).fetchone():
                raise Fenced("API request already admitted; retry forbidden")
            c.execute(
                "UPDATE trials SET ledger_revision=ledger_revision+1 WHERE trial_id=%s",
                (row["trial_id"],),
            )
            self._event(
                c,
                row,
                "model.started:" + operation,
                {"call_operation_id": operation, "reserved_usd": reserve},
            )
        return operation

    def checkpoint_prepare(self, expected, context):
        checkpoint = str(uuid5(NAMESPACE_URL, expected["execution_id"] + ":checkpoint"))
        with self.owned(expected) as (c, row):
            c.execute(
                "INSERT INTO "
                "checkpoints(checkpoint_id,trial_id,execution_id,epoch,stage,context,parent) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (
                    checkpoint,
                    row["trial_id"],
                    row["execution_id"],
                    row["epoch"],
                    row["stage"],
                    Jsonb(context),
                    row["resume_from"],
                ),
            )
            cp = c.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=%s", (checkpoint,)
            ).fetchone()
            if cp["context"] != context or cp["state"] not in {"preparing", "committed"}:
                raise ValueError("Checkpoint operation conflict")
            return cp

    def checkpoint_commit(self, expected, checkpoint, digest, context):
        with self.owned(expected) as (c, row):
            cp = c.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=%s FOR UPDATE", (checkpoint,)
            ).fetchone()
            if not cp or cp["execution_id"] != row["execution_id"] or cp["context"] != context:
                raise ValueError("Checkpoint identity/context mismatch")
            if cp["state"] == "committed":
                if cp["manifest_sha256"] != digest:
                    raise ValueError("Checkpoint digest changed")
                return cp
            if cp["state"] != "preparing" or row["ledger_revision"] != context["ledger_revision"]:
                raise Fenced("Checkpoint no longer matches the admitted boundary")
            if c.execute(
                "SELECT 1 FROM operations WHERE execution_id=%s AND state='pending'",
                (row["execution_id"],),
            ).fetchone():
                raise ValueError("Pending invocation cannot be checkpointed")
            c.execute(
                "UPDATE checkpoints SET "
                "state='committed',manifest_sha256=%s,committed_at=clock_timestamp() "
                "WHERE checkpoint_id=%s",
                (digest, checkpoint),
            )
            c.execute(
                "UPDATE trials SET latest_checkpoint=%s WHERE trial_id=%s",
                (checkpoint, row["trial_id"]),
            )
            self._event(
                c, row, "checkpoint.committed", {"checkpoint_id": checkpoint, "sha256": digest}
            )
            return c.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=%s", (checkpoint,)
            ).fetchone()

    def checkpoint(self, checkpoint):
        with self.connect() as c:
            cp = c.execute(
                "SELECT p.* FROM checkpoints p JOIN trials t USING(trial_id) "
                "WHERE p.checkpoint_id=%s "
                "AND t.root=%s AND p.state='committed'",
                (checkpoint, str(self.settings.root)),
            ).fetchone()
            if not cp:
                raise ValueError("Checkpoint is not committed or has been collected")
            return cp

    def finish(self, expected, result, *, controller=False):
        with self.owned(expected, live=not controller, allow_cancel=True) as (c, row):
            if row["status"] in TERMINAL:
                return
            self._unknown(c, row, result["reason"])
            status = "cancelled" if row["cancel_requested"] else result["status"]
            if status not in TERMINAL | {"checkpointed", "sealed"}:
                raise ValueError("Invalid outcome")
            if status in {"checkpointed", "sealed", "succeeded"}:
                if not c.execute(
                    "SELECT 1 FROM checkpoints WHERE checkpoint_id=%s AND execution_id=%s "
                    "AND state='committed'",
                    (row["latest_checkpoint"], row["execution_id"]),
                ).fetchone():
                    raise ValueError("Successful stages require their committed checkpoint")
            result = {**result, "status": status}
            c.execute(
                "UPDATE executions SET "
                "state='finished',cleanup='complete',result=%s WHERE execution_id=%s",
                (Jsonb(result), row["execution_id"]),
            )
            c.execute(
                "UPDATE trials SET status=%s,cleanup='complete',result=%s WHERE trial_id=%s",
                (status, Jsonb(result), row["trial_id"]),
            )
            self._event(c, row, "trial.finished", result)

    def advance(self, expected, key, *, recovery):
        if not key.strip() or len(key) > 200:
            raise ValueError("Recovery key requires 1–200 characters")
        operation = str(uuid5(NAMESPACE_URL, expected["trial_id"] + ":advance:" + key))
        with self.connect() as c:
            row = self._row(c, expected["trial_id"], True)
            prior = c.execute(
                "SELECT * FROM executions WHERE operation_id=%s", (operation,)
            ).fetchone()
            if prior:
                return prior
            if row["execution_id"] != expected["execution_id"] or row["epoch"] != expected["epoch"]:
                raise Fenced("Recovery raced with another epoch")
            if (
                row["artifacts_retired"]
                or row["cancel_requested"]
                or row["status"] in {"succeeded", "cancelled"}
            ):
                raise Fenced("Trial does not admit another execution")
            if c.execute(
                "SELECT deadline<=clock_timestamp() AS expired FROM trials WHERE trial_id=%s",
                (row["trial_id"],),
            ).fetchone()["expired"]:
                raise ValueError("Trial TTL exhausted; recovery does not reset it")
            cp = c.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=%s AND state='committed' FOR UPDATE",
                (row["latest_checkpoint"],),
            ).fetchone()
            if not cp:
                raise ValueError("No committed checkpoint available")
            if recovery and row["recoveries"] >= row["spec"].get("max_recoveries", 0):
                raise ValueError("Recovery allowance exhausted")
            if not recovery and row["status"] != "checkpointed":
                raise ValueError("Only a completed stage can advance normally")
            self._unknown(c, row, "execution_superseded")
            new_id = str(uuid5(NAMESPACE_URL, operation + ":execution"))
            c.execute(
                "UPDATE executions SET state='retired' WHERE execution_id=%s",
                (row["execution_id"],),
            )
            c.execute(
                "INSERT INTO "
                "executions(execution_id,trial_id,attempt_id,spec,handoff_id,epoch,stage,"
                "resume_from,operation_id) "
                "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (
                    new_id,
                    row["trial_id"],
                    row["attempt_id"],
                    Jsonb(row["spec"]),
                    row["handoff_id"],
                    row["epoch"] + 1,
                    cp["stage"] + 1,
                    cp["checkpoint_id"],
                    operation,
                ),
            )
            c.execute(
                "UPDATE trials SET execution_id=%s,epoch=epoch+1,status='queued',cleanup='pending',"
                "result=NULL,recoveries=recoveries+%s WHERE trial_id=%s",
                (new_id, int(recovery), row["trial_id"]),
            )
            new = self._row(c, row["trial_id"])
            self._event(
                c,
                new,
                "execution.recovered" if recovery else "execution.advanced",
                {
                    "from_execution": row["execution_id"],
                    "checkpoint_id": cp["checkpoint_id"],
                    "discarded_stage": row["stage"],
                    "loss_window": "since last committed stage",
                },
            )
            return new

    def recovery_result(self, trial_id, key):
        self.get(trial_id)
        operation = str(uuid5(NAMESPACE_URL, trial_id + ":advance:" + key))
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM executions WHERE operation_id=%s", (operation,)
            ).fetchone()

    def retire(self, trial_id):
        with self.connect() as c:
            row = self._row(c, trial_id, True)
            if row["status"] not in TERMINAL or row["cleanup"] != "complete":
                raise ValueError("Only terminal, cleaned trials can release artifact references")
            c.execute("UPDATE trials SET artifacts_retired=TRUE WHERE trial_id=%s", (trial_id,))
            c.execute(
                "DELETE FROM checkpoint_refs WHERE owner IN "
                "(SELECT 'handoff:' || handoff_id FROM handoffs WHERE target_trial=%s)",
                (trial_id,),
            )

    def executions(self, trial_id):
        trial = self.get(trial_id)
        with self.connect() as c:
            rows = c.execute(
                "SELECT * FROM executions WHERE trial_id=%s ORDER BY epoch", (trial_id,)
            ).fetchall()
        return [
            {
                **trial,
                **r,
                "execution_state": r["state"],
                "operation_id": trial["operation_id"],
                "execution_operation_id": r["operation_id"],
            }
            for r in rows
        ]

    def cleaned(self, execution_id):
        with self.connect() as c:
            c.execute(
                "UPDATE executions SET cleanup='complete' WHERE execution_id=%s", (execution_id,)
            )

    def pending(self):
        with self.connect() as c:
            ids = c.execute(
                "SELECT trial_id FROM trials WHERE root=%s AND "
                "(cleanup='pending' OR status IN ('checkpointed','sealed','preparing') "
                "OR EXISTS (SELECT 1 FROM executions e WHERE "
                "e.trial_id=trials.trial_id AND (e.cleanup='pending' OR e.state='retired')))",
                (str(self.settings.root),),
            ).fetchall()
            return [self._row(c, r["trial_id"]) for r in ids]

    def events(self, trial_id):
        self.get(trial_id)
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM events WHERE trial_id=%s ORDER BY sequence", (trial_id,)
            ).fetchall()

    def pin(self, checkpoint, owner, *, remove=False):
        if not owner.strip() or len(owner) > 200:
            raise ValueError("Reference owner requires 1–200 characters")
        with self.connect() as c:
            cp = c.execute(
                "SELECT p.* FROM checkpoints p JOIN trials t USING(trial_id) "
                "WHERE p.checkpoint_id=%s "
                "AND t.root=%s FOR UPDATE OF p",
                (checkpoint, str(self.settings.root)),
            ).fetchone()
            if not cp or cp["state"] != "committed":
                raise ValueError("Only committed artifacts can be referenced")
            if remove:
                c.execute(
                    "DELETE FROM checkpoint_refs WHERE checkpoint_id=%s AND owner=%s",
                    (checkpoint, owner),
                )
            else:
                c.execute(
                    "INSERT INTO checkpoint_refs VALUES(%s,%s) ON CONFLICT DO NOTHING",
                    (checkpoint, owner),
                )

    def gc_candidates(self, age_seconds):
        if age_seconds < 0:
            raise ValueError("Retention age cannot be negative")
        with self.connect() as c:
            # Lock order matches commit/advance: trial first, checkpoint second.
            c.execute(
                "SELECT trial_id FROM trials WHERE root=%s ORDER BY trial_id FOR UPDATE",
                (str(self.settings.root),),
            )
            rows = c.execute(
                "SELECT p.* FROM checkpoints p JOIN trials t USING(trial_id) WHERE t.root=%s "
                "AND p.created_at<clock_timestamp()-(%s * interval '1 second') "
                "ORDER BY p.checkpoint_id FOR UPDATE OF p",
                (str(self.settings.root), age_seconds),
            ).fetchall()
            selected = []
            for cp in rows:
                protected = c.execute(
                    "SELECT 1 WHERE EXISTS(SELECT 1 FROM trials WHERE latest_checkpoint=%s "
                    "AND NOT artifacts_retired) "
                    "OR EXISTS(SELECT 1 FROM executions e JOIN trials t USING(trial_id) "
                    "WHERE e.resume_from=%s AND NOT t.artifacts_retired) "
                    "OR EXISTS(SELECT 1 FROM checkpoint_refs WHERE checkpoint_id=%s) "
                    "OR EXISTS(SELECT 1 FROM checkpoints WHERE parent=%s AND "
                    "state NOT IN ('deleted','deleting')) "
                    "OR EXISTS(SELECT 1 FROM executions e JOIN trials t ON "
                    "t.execution_id=e.execution_id "
                    "WHERE e.execution_id=%s AND e.state='running' AND "
                    "e.lease_until>clock_timestamp())",
                    (*([cp["checkpoint_id"]] * 4), cp["execution_id"]),
                ).fetchone()
                if not protected:
                    c.execute(
                        "UPDATE checkpoints SET state='deleting' WHERE checkpoint_id=%s",
                        (cp["checkpoint_id"],),
                    )
                    selected.append(cp)
            return selected

    def gc_done(self, checkpoint):
        with self.connect() as c:
            c.execute(
                "UPDATE checkpoints SET state='deleted' WHERE checkpoint_id=%s "
                "AND state='deleting'",
                (checkpoint,),
            )
