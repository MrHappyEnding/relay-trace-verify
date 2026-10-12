"""Temporal registration and the PostgreSQL → Workflow reconciliation loop."""

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from temporalio import activity
from temporalio.client import Client
from temporalio.common import WorkflowIDConflictPolicy, WorkflowIDReusePolicy
from temporalio.exceptions import WorkflowAlreadyStartedError
from temporalio.worker import Worker

from rtv.runtime.config import TERMINAL, Settings
from rtv.runtime.control import reconcile
from rtv.runtime.store import Store
from rtv.runtime.workflow import TrialWorkflow


async def ensure_workflow(client: Client, settings: Settings, trial_id: str):
    if (await asyncio.to_thread(Store(settings).get, trial_id))["status"] in TERMINAL:
        return
    try:
        await client.start_workflow(
            TrialWorkflow.run,
            trial_id,
            id=settings.schema + "-" + trial_id,
            task_queue=settings.queue,
            id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
            id_reuse_policy=WorkflowIDReusePolicy.ALLOW_DUPLICATE,
        )
    except WorkflowAlreadyStartedError:
        # Concurrent dispatch uses the running controller. A deliberately recovered
        # product trial may start a new control run after its prior run completed.
        pass


class Activities:
    def __init__(self, settings: Settings):
        self.settings = settings

    @activity.defn(name="rtv_reconcile")
    def poll(self, trial_id: str) -> dict:
        return reconcile(self.settings, trial_id)

    @activity.defn(name="rtv_cancel")
    def cancel(self, trial_id: str):
        Store(self.settings).cancel(trial_id)


async def serve(settings: Settings):
    store = Store(settings)
    store.initialize()
    client = await Client.connect(settings.temporal)
    activities = Activities(settings)

    async def repair():
        while True:
            try:
                # Covers DB commit → start_workflow acknowledgement loss, expired
                # queued work, and cleanup after a supervisor/Workflow disappears.
                for row in await asyncio.to_thread(store.pending):
                    try:
                        await asyncio.to_thread(reconcile, settings, row["trial_id"], launch=False)
                        await ensure_workflow(client, settings, row["trial_id"])
                    except Exception as exc:
                        logging.warning(
                            "Reconciliation deferred for %s: %s",
                            row["trial_id"],
                            type(exc).__name__,
                        )
            except Exception as exc:
                logging.warning("Metadata scan deferred: %s", type(exc).__name__)
            await asyncio.sleep(3)

    with ThreadPoolExecutor(max_workers=4) as pool:
        async with Worker(
            client,
            task_queue=settings.queue,
            workflows=[TrialWorkflow],
            activities=[activities.poll, activities.cancel],
            activity_executor=pool,
            max_concurrent_activities=4,
            graceful_shutdown_timeout=timedelta(seconds=5),
        ):
            await repair()
