"""Transactional handoff and paired-branch identities above the fenced runtime."""

from uuid import NAMESPACE_URL, uuid5

from psycopg.types.json import Jsonb

from rtv.runtime.store import Fenced, Store, identity, spec_hash


def stable(value):
    return str(uuid5(NAMESPACE_URL, value))


class RelayStore(Store):
    def handoff(self, handoff_id):
        with self.connect() as c:
            h = c.execute(
                "SELECT * FROM handoffs WHERE handoff_id=%s AND root=%s",
                (handoff_id, str(self.settings.root)),
            ).fetchone()
            if not h:
                raise ValueError("Unknown handoff")
            return h

    def request(self, expected, key, target, policy):
        hid = stable(expected["trial_id"] + ":handoff:" + key)
        digest = spec_hash({"target": target, "policy": policy})
        with self.connect() as c:
            row = self._row(c, expected["trial_id"], True)
            prior = c.execute("SELECT * FROM handoffs WHERE handoff_id=%s", (hid,)).fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise ValueError("Handoff key conflicts with prior request")
                return prior
            self._sealed(c, row, expected)
            self._insert(
                c,
                hid,
                digest,
                row,
                row["trial_id"],
                stable(hid + ":attempt"),
                stable(hid + ":execution"),
                target,
                policy,
                "relay",
            )
            c.execute("UPDATE trials SET status='preparing' WHERE trial_id=%s", (row["trial_id"],))
            self._event(c, row, "handoff.requested:" + hid, {"handoff_id": hid, "policy": policy})
        return self.handoff(hid)

    @staticmethod
    def _sealed(c, row, expected):
        if any(row[k] != expected[k] for k in ("epoch", "execution_id", "attempt_id")):
            raise Fenced("Source changed before handoff")
        if row["status"] != "sealed" or row["cancel_requested"] or row["artifacts_retired"]:
            raise Fenced("Source must be sealed, retained and not cancelled")
        if c.execute(
            "SELECT deadline<=clock_timestamp() AS expired FROM trials WHERE trial_id=%s",
            (row["trial_id"],),
        ).fetchone()["expired"]:
            raise Fenced("Source TTL expired")
        cp = c.execute(
            "SELECT * FROM checkpoints WHERE checkpoint_id=%s AND state='committed' FOR UPDATE",
            (row["latest_checkpoint"],),
        ).fetchone()
        if not cp or cp["execution_id"] != row["execution_id"]:
            raise Fenced("Source checkpoint is not its sealed execution")
        return cp

    def _insert(self, c, hid, digest, source, target_trial, attempt, execution, spec, policy, kind):
        c.execute(
            "INSERT INTO handoffs(handoff_id,root,request_hash,kind,source_trial,source_execution,"
            "source_attempt,source_epoch,checkpoint_id,target_trial,target_attempt,"
            "target_execution,target_spec,policy) "
            "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                hid,
                str(self.settings.root),
                digest,
                kind,
                source["trial_id"],
                source["execution_id"],
                source["attempt_id"],
                source["epoch"],
                source["latest_checkpoint"],
                target_trial,
                attempt,
                execution,
                Jsonb(spec),
                policy,
            ),
        )
        c.execute(
            "INSERT INTO checkpoint_refs VALUES(%s,%s)",
            (source["latest_checkpoint"], "handoff:" + hid),
        )

    def create_experiment(self, expected, key, branches, ttl):
        eid = stable(str(self.settings.root) + ":experiment:" + key)
        config = {"checkpoint": expected["latest_checkpoint"], "branches": branches, "ttl": ttl}
        digest = spec_hash(config)
        with self.connect() as c:
            source = self._row(c, expected["trial_id"], True)
            prior = c.execute("SELECT * FROM experiments WHERE experiment_id=%s", (eid,)).fetchone()
            if prior:
                if prior["request_hash"] != digest:
                    raise ValueError("Experiment key conflicts with its frozen configuration")
                return eid
            cp = self._sealed(c, source, expected)
            if source["parent_checkpoint"]:
                raise ValueError("Nested experiment forks are not enabled in M3")
            c.execute(
                "INSERT INTO experiments(experiment_id,root,request_hash,source_checkpoint,config) "
                "VALUES(%s,%s,%s,%s,%s)",
                (eid, str(self.settings.root), digest, cp["checkpoint_id"], Jsonb(config)),
            )
            prefix = [
                r["operation_id"]
                for r in c.execute(
                    "SELECT operation_id FROM operations WHERE trial_id=%s", (source["trial_id"],)
                ).fetchall()
            ]
            for branch in branches:
                label, target, policy = branch["label"], branch["spec"], branch["policy"]
                ids = identity(str(self.settings.root), eid + ":" + label)
                hid = stable(eid + ":handoff:" + label)
                c.execute(
                    "INSERT INTO"
                    " trials(trial_id,operation_id,attempt_id,execution_id,epoch,"
                    "root,spec,spec_hash,"
                    "deadline,status,parent_checkpoint,experiment_id,branch_label,"
                    "prefix_operations) "
                    "VALUES(%s,%s,%s,%s,1,%s,%s,%s,clock_timestamp()+(%s*interval '1 second'),"
                    "'preparing',%s,%s,%s,%s)",
                    (
                        ids["trial_id"],
                        ids["operation_id"],
                        ids["attempt_id"],
                        ids["execution_id"],
                        str(self.settings.root),
                        Jsonb(target),
                        spec_hash(target),
                        ttl,
                        cp["checkpoint_id"],
                        eid,
                        label,
                        Jsonb(prefix),
                    ),
                )
                c.execute(
                    "INSERT INTO attempts(attempt_id,trial_id,spec) VALUES(%s,%s,%s)",
                    (ids["attempt_id"], ids["trial_id"], Jsonb(target)),
                )
                c.execute(
                    "INSERT INTO"
                    " executions(execution_id,trial_id,attempt_id,spec,"
                    "handoff_id,epoch,stage,resume_from,operation_id,state) "
                    "VALUES(%s,%s,%s,%s,%s,1,%s,%s,%s,'held')",
                    (
                        ids["execution_id"],
                        ids["trial_id"],
                        ids["attempt_id"],
                        Jsonb(target),
                        hid,
                        cp["stage"] + 1,
                        cp["checkpoint_id"],
                        ids["operation_id"],
                    ),
                )
                self._insert(
                    c,
                    hid,
                    spec_hash(branch),
                    source,
                    ids["trial_id"],
                    ids["attempt_id"],
                    ids["execution_id"],
                    target,
                    policy,
                    "branch",
                )
                child = self._row(c, ids["trial_id"])
                self._event(
                    c,
                    child,
                    "branch.created",
                    {"experiment_id": eid, "checkpoint_id": cp["checkpoint_id"], "handoff_id": hid},
                )
        return eid

    def mark_prepared(self, hid, prepared):
        h = self.handoff(hid)
        with self.connect() as c:
            row = self._row(c, h["target_trial"], True)
            current = c.execute(
                "SELECT * FROM handoffs WHERE handoff_id=%s FOR UPDATE", (hid,)
            ).fetchone()
            if row["cancel_requested"] or current["state"] == "aborted":
                raise Fenced("Target cancelled during preparation")
            if current["state"] == "requested":
                c.execute(
                    "UPDATE handoffs SET state='prepared',prepared=%s WHERE handoff_id=%s",
                    (Jsonb(prepared), hid),
                )
                self._event(c, row, "handoff.prepared:" + hid, {"handoff_id": hid, **prepared})
            elif current["prepared"] != prepared:
                raise ValueError("Prepared target content changed")

    def activate(self, hid):
        h = self.handoff(hid)
        with self.connect() as c:
            c.execute(
                "SELECT trial_id FROM trials WHERE trial_id=ANY(%s) ORDER BY trial_id FOR UPDATE",
                ([h["source_trial"], h["target_trial"]],),
            )
            row = self._row(c, h["target_trial"], True)
            h = c.execute(
                "SELECT * FROM handoffs WHERE handoff_id=%s FOR UPDATE", (hid,)
            ).fetchone()
            if h["state"] == "completed":
                return row
            if h["state"] != "prepared" or row["cancel_requested"] or row["artifacts_retired"]:
                raise Fenced("Handoff is not ready for activation")
            if c.execute(
                "SELECT deadline<=clock_timestamp() AS expired FROM trials WHERE trial_id=%s",
                (row["trial_id"],),
            ).fetchone()["expired"]:
                raise Fenced("Target TTL expired during preparation")
            cp = c.execute(
                "SELECT * FROM checkpoints WHERE checkpoint_id=%s AND state='committed' FOR UPDATE",
                (h["checkpoint_id"],),
            ).fetchone()
            if not cp:
                raise Fenced("Source checkpoint unavailable")
            if h["kind"] == "relay":
                if (
                    row["execution_id"] != h["source_execution"]
                    or row["attempt_id"] != h["source_attempt"]
                ):
                    raise Fenced("Source authority changed")
                c.execute(
                    "INSERT INTO attempts(attempt_id,trial_id,spec) VALUES(%s,%s,%s)",
                    (h["target_attempt"], row["trial_id"], Jsonb(h["target_spec"])),
                )
                c.execute(
                    "UPDATE executions SET state='retired' WHERE execution_id=%s",
                    (row["execution_id"],),
                )
                c.execute(
                    "INSERT INTO"
                    " executions(execution_id,trial_id,attempt_id,spec,"
                    "handoff_id,epoch,stage,resume_from,operation_id) "
                    "VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        h["target_execution"],
                        row["trial_id"],
                        h["target_attempt"],
                        Jsonb(h["target_spec"]),
                        hid,
                        row["epoch"] + 1,
                        cp["stage"] + 1,
                        cp["checkpoint_id"],
                        hid,
                    ),
                )
                c.execute(
                    "UPDATE trials SET attempt_id=%s,execution_id=%s,epoch=epoch+1,spec=%s"
                    " WHERE trial_id=%s",
                    (
                        h["target_attempt"],
                        h["target_execution"],
                        Jsonb(h["target_spec"]),
                        row["trial_id"],
                    ),
                )
            else:
                c.execute(
                    "UPDATE executions SET state='queued' WHERE execution_id=%s AND state='held'",
                    (row["execution_id"],),
                )
            c.execute(
                "UPDATE trials SET status='queued',cleanup='pending',result=NULL WHERE trial_id=%s",
                (row["trial_id"],),
            )
            c.execute(
                "UPDATE handoffs SET state='completed',completed_at=clock_timestamp()"
                " WHERE handoff_id=%s",
                (hid,),
            )
            row = self._row(c, row["trial_id"])
            self._event(
                c,
                row,
                "handoff.completed",
                {
                    "handoff_id": hid,
                    "source_attempt": h["source_attempt"],
                    "checkpoint_id": h["checkpoint_id"],
                    "policy": h["policy"],
                },
            )
            return row

    def abort(self, hid, error):
        h = self.handoff(hid)
        with self.connect() as c:
            row = self._row(c, h["target_trial"], True)
            changed = c.execute(
                "UPDATE handoffs SET state='aborted',error=%s WHERE handoff_id=%s "
                "AND state IN ('requested','prepared') RETURNING handoff_id",
                (error, hid),
            ).fetchone()
            if changed:
                if (
                    h["kind"] == "relay"
                    and not row["cancel_requested"]
                    and row["status"] == "preparing"
                ):
                    c.execute(
                        "UPDATE trials SET status='sealed' WHERE trial_id=%s", (row["trial_id"],)
                    )
                c.execute("DELETE FROM checkpoint_refs WHERE owner=%s", ("handoff:" + hid,))
                self._event(c, row, "handoff.aborted:" + hid, {"handoff_id": hid, "error": error})

    def pending_handoff(self, trial_id):
        with self.connect() as c:
            return c.execute(
                "SELECT h.* FROM handoffs h JOIN trials t ON h.target_trial=t.trial_id "
                "WHERE t.root=%s AND target_trial=%s AND h.state IN ('requested','prepared')",
                (str(self.settings.root), trial_id),
            ).fetchone()

    def experiments(self):
        with self.connect() as c:
            return c.execute(
                "SELECT * FROM experiments WHERE root=%s ORDER BY created_at DESC",
                (str(self.settings.root),),
            ).fetchall()

    def experiment(self, eid):
        with self.connect() as c:
            e = c.execute(
                "SELECT * FROM experiments WHERE experiment_id=%s AND root=%s",
                (eid, str(self.settings.root)),
            ).fetchone()
            if not e:
                raise ValueError("Unknown experiment")
            rows = c.execute(
                "SELECT trial_id FROM trials WHERE experiment_id=%s ORDER BY branch_label", (eid,)
            ).fetchall()
        return {**e, "trials": [self.get(r["trial_id"]) for r in rows]}
