from uuid import uuid4

import pytest

from rtv.domain import ExecutionRef, Trial, TrialStatus


def test_recovery_keeps_attempt_and_advances_execution_epoch():
    source = ExecutionRef(trial_id=uuid4())
    recovered = source.recover()
    assert recovered.trial_id == source.trial_id
    assert recovered.attempt_id == source.attempt_id
    assert recovered.execution_id != source.execution_id
    assert recovered.epoch == source.epoch + 1


def test_handoff_creates_a_new_attempt_in_same_trial():
    source = ExecutionRef(trial_id=uuid4(), epoch=3)
    target = source.handoff()
    assert target.trial_id == source.trial_id
    assert target.attempt_id != source.attempt_id
    assert target.execution_id != source.execution_id
    assert target.epoch == 1


@pytest.mark.parametrize(
    "status", [TrialStatus.SUCCEEDED, TrialStatus.FAILED, TrialStatus.CANCELLED]
)
def test_terminal_trial_cannot_restart(status):
    with pytest.raises(ValueError, match="Invalid trial transition"):
        Trial(task="fixture", status=status).transition(TrialStatus.RUNNING)


def test_cancellation_blocks_new_running_or_verifying_states():
    trial = Trial(task="fixture").transition(TrialStatus.RUNNING)
    cancelled = trial.transition(TrialStatus.CANCEL_REQUESTED)
    for target in (TrialStatus.RUNNING, TrialStatus.VERIFYING, TrialStatus.SUCCEEDED):
        with pytest.raises(ValueError):
            cancelled.transition(target)
    assert cancelled.transition(TrialStatus.CANCELLED).status == TrialStatus.CANCELLED


def test_success_requires_verification():
    trial = Trial(task="fixture").transition(TrialStatus.RUNNING)
    with pytest.raises(ValueError):
        trial.transition(TrialStatus.SUCCEEDED)
    assert trial.transition(TrialStatus.VERIFYING).transition(TrialStatus.SUCCEEDED)
