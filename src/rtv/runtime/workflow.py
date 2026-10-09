"""Temporal owns durable control flow; it never runs or replays an agent turn."""

import asyncio
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy


@workflow.defn
class TrialWorkflow:
    def __init__(self):
        self.state = {"status": "queued"}

    @workflow.query
    def status(self) -> dict:
        return self.state

    @workflow.run
    async def run(self, trial_id: str) -> dict:
        while True:
            try:
                self.state = await workflow.execute_activity(
                    "rtv_reconcile",
                    trial_id,
                    start_to_close_timeout=timedelta(seconds=45),
                    retry_policy=RetryPolicy(
                        initial_interval=timedelta(seconds=1), maximum_interval=timedelta(seconds=5)
                    ),
                )
                if self.state["status"] in {"succeeded", "failed", "cancelled"}:
                    return self.state
                await workflow.sleep(2)
            except asyncio.CancelledError:
                # Native Temporal cancel is also translated to durable product intent.
                await workflow.execute_activity(
                    "rtv_cancel",
                    trial_id,
                    start_to_close_timeout=timedelta(seconds=15),
                )
