# M0: First Real Agent and Recovery Boundaries

[![中文](assets/lang-zh.svg)](codex-m0.md) [![English](assets/lang-en.svg)](codex-m0.en.md)

2026-10-08: Codex native-session recovery and fresh-session handoff passed on local
ARM64 Docker. This is a controlled compatibility experiment, not a two-agent
performance experiment or completion of all M0 acceptance criteria.

## Configuration and attribution

| Component | Configuration / owner |
| --- | --- |
| Agent | Official Codex CLI 0.160.1, `gpt-6-astra`, reasoning effort `low` |
| Model access | Existing ChatGPT subscription login; no metered API enabled |
| Harbor | 0.24.0; reuses Codex adapter, DockerEnvironment, Task and Verifier |
| Containers | Linux/ARM64, Engine 28.5.1, Compose 2.40.0-desktop.1; 1 CPU / 1 GiB |
| RTV | Checkpoint packaging/integrity, recovery/handoff identities, quiescence, protocol assertions and usage attribution |

The Python base image is pinned by digest. Official Codex platform packages are
pinned by version and SHA-512 and installed as native executables without remote
installer scripts. The installer also pins x64, but only ARM64 was tested here.
Recovery branches use the exact source image ID. Harbor's network sidecar base
image is not yet pinned by digest.

## Commands

Run from the repository root, choosing a new output directory each time:

```bash
uv sync --python 3.13 --locked --group harbor --dev
# Build/check/clean the container. No credentials or model calls.
uv run --no-sync rtv codex-prepare --output work/codex-prepare-1

# Uses subscription quota. Confirm the login account and available quota first.
uv run --no-sync rtv codex-smoke \
  --auth-file "$HOME/.codex/auth.json" \
  --model gpt-6-astra --allow-model-calls \
  --turn-timeout 240 --output work/codex-smoke-1
```

Model execution requires `--allow-model-calls`. Only a ChatGPT cache containing
tokens and no API key is accepted. This is not a general API/lab endpoint adapter.
An expired login or unavailable model fails and cleans up without changing models
or automatically retrying. New paid services remain subject to the
[spending agreement](learning-plan.en.md).

Credentials are copied temporarily into the container and removed by Harbor after
execution; the entire container is also deleted. Only this run's independent agent
and verifier log directories are mounted, with no host home or Docker socket mount.
Raw sessions, prompts, code and events stay in ignored `work/` and should not be
published. Agent egress is limited to `chatgpt.com`, `auth.openai.com` and
`api.openai.com`; verification switches to `no-network`.

## What the three turns establish

1. **Source stage:** real Codex implements positive filtering while deliberately
   retaining an empty-input defect. It remembers a random marker without writing
   it to the workspace. The independent verifier rejects the partial solution.
2. **Checkpoint:** a controlled background writer is started and observed writing.
   The whole container is stopped and its state checked. Two exports must hash
   identically. `/workspace`, one native rollout, and version/session metadata
   enter one manifest-protected checkpoint. Credentials are outside its scope.
3. **Native recovery:** a fresh container imports files and native history. Trial
   and Attempt remain unchanged; Execution and epoch advance. It must retain the
   session ID, recall the marker absent from its new prompt/workspace, finish the
   task and pass verification.
4. **Semantic handoff:** another fresh container receives the same workspace and a
   structured handoff, without native history. Trial remains; Attempt, Execution
   and session ID change. It must also finish and pass verification.
5. **Isolation/cleanup:** pre-execution file hashes match, branch edits leave the
   source unchanged, and every container created by this run is deleted.

Both receiving branches use **the same CLI and model**. The harness constructs the
handoff packet; automatic summary quality is not evaluated. A second agent
implementation remains part of later M3 experiments.

Harbor `load()` uses `resume --last`. RTV imports exactly one rollout into a new
container's isolated session directory and checks the returned session ID, avoiding
selection of another user session. Missing history, incompatible versions/models,
corrupt files or an unstopped source never silently fall back to a fresh session.

## Evidence and usage

The [2026-10-08 report](evidence/m0-codex-arm64-20261008.json) records three completed
turns, 17 passing assertions, about 105 seconds elapsed, reward 1 for both receiving
branches and no cleanup errors. Raw artifacts: `work/codex-smoke-20261008-2/`.

Codex 0.160.1 `turn.completed.usage` is **session-cumulative after resume**. RTV
retains `session_usage` and subtracts imported counters to obtain incremental
`usage`. Missing, reset or incompatible counters remain unknown rather than zero.
The successful probe consumed 134,682 input tokens (115,200 cached) and 1,200
output tokens. Input includes repeated context across model requests; it is not
the length of new user text. Cached tokens are a subset of input tokens.

An initial development run completed the source turn but failed because RTV's
container helper did not handle `stdout=None`. This was fixed and regression-tested.
Including that run, development completed four agent turns: 182,758 input tokens
(150,528 cached) and 1,569 output tokens. An agent turn can issue multiple model
requests. Existing subscription quota was used without enabling additional API
billing; dollar cost remains `null`, without inventing a subscription token price.
The committed evidence reconciles retained logs offline to correct cumulative
accounting; raw execution reports were not rewritten and models were not rerun.

## Guarantees and remaining boundaries

| Situation | Current behavior / limitation |
| --- | --- |
| Completed stage | Require a completed event and unique rollout; shell pipeline success alone is insufficient |
| Source stop | Stop all container processes, including the controlled background writer, before file export |
| Timeout/cancellation | Stop the sandbox, do not checkpoint the incomplete stage, clean up and report; protocol faults are mocked tests |
| Native recovery | Pinned CLI/model, same image and `/workspace`; restores files and history only |
| Corruption/version mismatch/multiple sessions | Reject without silently retrying or dropping history |
| Budget | At most three agent turns and a per-turn wall clock; no per-request interception or strict token/dollar admission |
| Worker crash | No durable orchestration, lease or epoch fencing; local finally cleanup is not a crash-recovery service |
| Processes/ATIF/second agent | No process memory or pending-tool recovery; ATIF import, cross-model and cross-framework handoff unverified |

Only trusted, reconstructible filesystem tasks are supported. Verification runs
inside the target container rather than an isolated hidden-test service. Successful
export after stage completion does not establish arbitrary mid-tool recovery.

Next: finish M0 with ATIF import/information-loss tests, real interruption scenarios
and capability declarations, then M1 durable single-agent execution. Add the second
agent for paired experiments after M1/M2.

## References

- [Codex non-interactive execution and resume](https://learn.chatgpt.com/docs/non-interactive-mode)
- [Codex authentication and container login caches](https://learn.chatgpt.com/docs/auth)
- [Harbor trajectory loading](https://docs.harborframework.com/jobs/loading-trajectories)

Native import reuses Harbor/Codex functionality. RTV adds verifiable file
consistency, identity and failure boundaries; upstream implementations are not
claimed as original work.
