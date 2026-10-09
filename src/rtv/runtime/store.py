"""PostgreSQL product state and idempotent, correlated control/execution events."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from rtv.runtime.config import TERMINAL, Settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS rtv_trials (
    trial_id TEXT PRIMARY KEY,
    operation_id TEXT NOT NULL UNIQUE,
    attempt_id TEXT NOT NULL UNIQUE,
    execution_id TEXT NOT NULL UNIQUE,
    epoch INTEGER NOT NULL DEFAULT 1 CHECK (epoch = 1),
    root TEXT NOT NULL,
    spec JSONB NOT NULL,
    spec_hash TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued'
      CHECK (status IN ('queued','running','verifying','cancel_requested',
                        'succeeded','failed','cancelled')),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    cleanup TEXT NOT NULL DEFAULT 'pending' CHECK (cleanup IN ('pending','complete')),
    created_at TIMESTAMPTZ NOT NULL,
    deadline TIMESTAMPTZ NOT NULL,
    result JSONB
);
CREATE TABLE IF NOT EXISTS rtv_events (
    sequence BIGSERIAL PRIMARY KEY,
    trial_id TEXT NOT NULL REFERENCES rtv_trials(trial_id),
    event_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    payload JSONB NOT NULL,
    at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def identity(root: str, key: str) -> dict:
    if not key.strip() or len(key) > 200:
        raise ValueError("Idempotency key must contain 1–200 characters")
    operation = str(uuid5(NAMESPACE_URL, f"rtv:m1:{root}:{key}"))
    return {
        "operation_id": operation,
        **{
            name: str(uuid5(NAMESPACE_URL, f"{operation}:{name}"))
            for name in ("trial_id", "attempt_id", "execution_id")
        },
    }


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(json.dumps(spec, sort_keys=True).encode()).hexdigest()


class Store:
    def __init__(self, settings: Settings):
        self.settings = settings

    def connect(self):
        return psycopg.connect(
            self.settings.dsn,
            row_factory=dict_row,
            connect_timeout=5,
            options="-c statement_timeout=10000",
        )

    def initialize(self):
        with self.connect() as conn:
            conn.execute(SCHEMA)

    @staticmethod
    def _event(conn, row: dict, kind: str, payload: dict):
        payload = {
            **payload,
            **{k: row[k] for k in ("operation_id", "attempt_id", "execution_id", "epoch")},
        }
        conn.execute(
            "INSERT INTO rtv_events(trial_id,event_id,kind,payload) VALUES(%s,%s,%s,%s) "
            "ON CONFLICT(event_id) DO NOTHING",
            (row["trial_id"], row["execution_id"] + ":" + kind, kind, Jsonb(payload)),
        )

    def submit(self, key: str, spec: dict) -> dict:
        ids = identity(str(self.settings.root), key)
        now = datetime.now(UTC)
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO rtv_trials(trial_id,operation_id,attempt_id,execution_id,root,"
                "spec,spec_hash,created_at,deadline) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT(operation_id) DO NOTHING",
                (
                    *[ids[k] for k in ("trial_id", "operation_id", "attempt_id", "execution_id")],
                    str(self.settings.root),
                    Jsonb(spec),
                    spec_hash(spec),
                    now,
                    now + timedelta(seconds=spec["ttl_seconds"]),
                ),
            )
            row = conn.execute(
                "SELECT * FROM rtv_trials WHERE trial_id=%s FOR UPDATE", (ids["trial_id"],)
            ).fetchone()
            if row["spec_hash"] != spec_hash(spec):
                raise ValueError("Idempotency key already belongs to a different request")
            self._event(conn, row, "trial.submitted", {"spec_hash": row["spec_hash"]})
            return row

    def get(self, trial_id: str) -> dict:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM rtv_trials WHERE trial_id=%s AND root=%s",
                (trial_id, str(self.settings.root)),
            ).fetchone()
            if row is None:
                raise ValueError("Unknown trial in this runtime root")
            return row

    def event(self, trial_id: str, kind: str, payload: dict):
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM rtv_trials WHERE trial_id=%s", (trial_id,)).fetchone()
            self._event(conn, row, kind, payload)

    def phase(self, trial_id: str, phase: str, payload: dict):
        if phase not in {"running", "verifying"}:
            raise ValueError("Invalid execution phase")
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM rtv_trials WHERE trial_id=%s FOR UPDATE", (trial_id,)
            ).fetchone()
            if row["status"] in TERMINAL or row["cancel_requested"]:
                return
            allowed = {"running": {"queued"}, "verifying": {"running"}}
            if row["status"] not in allowed[phase] and row["status"] != phase:
                raise ValueError("Invalid runtime state transition")
            conn.execute("UPDATE rtv_trials SET status=%s WHERE trial_id=%s", (phase, trial_id))
            self._event(conn, row, "execution." + phase, payload)

    def cancel(self, trial_id: str) -> dict:
        self.get(trial_id)
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM rtv_trials WHERE trial_id=%s FOR UPDATE", (trial_id,)
            ).fetchone()
            if row["status"] not in TERMINAL:
                conn.execute(
                    "UPDATE rtv_trials SET cancel_requested=TRUE, "
                    "status='cancel_requested' WHERE trial_id=%s",
                    (trial_id,),
                )
                self._event(conn, row, "trial.cancel_requested", {})
        return self.get(trial_id)

    def finish(self, trial_id: str, result: dict):
        # The caller has already confirmed resource cleanup. Cancel intent wins
        # until this transaction publishes the terminal outcome.
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM rtv_trials WHERE trial_id=%s FOR UPDATE", (trial_id,)
            ).fetchone()
            if row["status"] in TERMINAL:
                return
            status = "cancelled" if row["cancel_requested"] else result["status"]
            if status not in TERMINAL:
                raise ValueError("Invalid final state")
            result = {**result, "status": status}
            conn.execute(
                "UPDATE rtv_trials SET status=%s,cleanup='complete',result=%s WHERE trial_id=%s",
                (status, Jsonb(result), trial_id),
            )
            self._event(conn, row, "trial.finished", result)

    def pending(self) -> list[dict]:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM rtv_trials WHERE root=%s AND cleanup='pending'",
                (str(self.settings.root),),
            ).fetchall()

    def events(self, trial_id: str) -> list[dict]:
        self.get(trial_id)
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM rtv_events WHERE trial_id=%s ORDER BY sequence", (trial_id,)
            ).fetchall()
