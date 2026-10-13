# M4: Real-repository relay and recovery integration

**Status: local runtime integration for the first SWE-bench development task passes model-free acceptance.**
Twenty-six real-container/PostgreSQL/Temporal checks pass. Mini-swe-agent receives scripted responses,
and repair actions derive from the upstream reference patch. This validates execution semantics,
not model problem-solving ability or relay effectiveness.

No GPU is required here. Current real-model paths use Codex subscription inference and a metered API;
the local machine runs containers, CPU tests, storage and orchestration. Lab GPUs remain useful for
later self-hosted inference or training. If moving to an x86_64 Linux lab environment, native task
architecture, memory and storage can be useful before enabling GPU inference.

## Runtime contract

`rtv benchmark prepare-task` binds the [frozen protocol](m4-benchmark.md) and a passing offline
preflight to a local contract: task identity, definition digest, `/testbed`, AMD64 platform, base
commit, agent/verifier image IDs, verifier timeout and explicit exclusions. It has its own
`contract_sha256`. Only the four development tasks are enabled; actual runtime evidence covers one.
Evaluation tasks remain gated. The full definition is checked at preparation, submission and
execution; handoff and recovery cannot change the contract or workspace.

- Prompts read only `instruction.md`, never hidden tests, reference solutions or hidden rewards.
- The prefix seals after a naturally completed turn, without hidden verification or an intentionally
  introduced defect. Only final branches run an isolated verifier.
- Mini tools and Codex session metadata use the declared cwd. Native recovery requires matching
  version, model and cwd. Unit tests cover the new path contract; the subsequent
  [real pilot](m4-model-pilot.md) verifies the session round trip on one repository.
- The verifier uses its pinned image offline, receiving only declared workspace data, without native
  agent state. Its default timeout is 600 seconds. SWE-bench parser output must agree with the reward;
  parser failures cannot masquerade as valid unresolved outcomes.
- The new mini path supports a 16-request fresh budget and 600-second stage cap. M3 defaults remain.
  `--ttl` is the absolute Trial lifetime including setup and verification; `--agent-seconds` applies
  to the agent stage. Full experiment scheduling, queue accounting and statistical linkage are pending.

## Snapshot and recovery boundaries

Repository snapshots preserve regular-file and directory permission bits `0o777`, empty directories,
`.git` and uncommitted files. Snapshots containing symbolic links use **manifest v4**, recording link
paths and exact target strings without copying their referents. Relative in-workspace links, chains
and dangling in-workspace targets are supported. Absolute, escaping and cyclic links are rejected.
Links are installed after regular files so restoration never writes through a link. Link-free
repositories retain v3; v1/v2 and legacy fixture behavior remain supported. Special files, UID/GID,
ACLs, xattrs and process memory remain unsupported.
Tasks depending on them fail closed; this is not compatibility evidence for every SWE-bench repository.

`.DS_Store` is the only permitted excluded name, recorded in the contract, manifest and recovery
context. It is excluded from workspace hashes and restoration; recovery/verifier containers remove
any copy introduced during host transfer before running. Git data, other hidden files and code are
not ignored. Tampered snapshot files still fail validation, with tests covering that distinction.
The stopped-container barrier remains mandatory; excluding host metadata does not permit other
external workspace writers.

A branch that has not published its own checkpoint can recover from its inherited committed input.
A new epoch/container/workspace retains the original deadline, discards unfinished changes and leaves
the interrupted call UNKNOWN. Acceptance kills only its own A-branch supervisor, then verifies
recovery and completion; unrelated user processes are untouched.

## Model-free reproduction

Requires running local PostgreSQL, Temporal and Docker plus the runtime dependency group.
The example uses the M4 PostgreSQL forwarded port, but runs a local supervisor under a separate root
and `rtv3` schema, not a Kubernetes Job. Existing `rtv4` records remain separate.
Complete the [environment preflight](m4-benchmark.md) first.

```bash
.venv/bin/python -m rtv benchmark prepare-task \
  --upstream work/m4-benchmark/harbor-datasets --task sympy__sympy-22914 \
  --preflight work/m4-benchmark/preflight-new/preflight.json \
  --output work/m4-benchmark/runtime-contract.json

RTV_DATABASE_URL=postgresql://rtv:rtv-local-cluster@127.0.0.1:55434/rtv \
  .venv/bin/python scripts/m4_repository_acceptance.py \
  --contract work/m4-benchmark/runtime-contract.json \
  --output work/m4-benchmark/repository-new
```

The script admits containers serially and creates one source Trial, three continuation branches and
one fresh Trial, with one additional Execution after fault recovery. It disables its worker's model
switches, retains failed attempts and cleans up owned containers/networks. The reference patch is an
explicit scripted control; never import these outcomes into a `real-model` report.

Install and version-check Codex without authentication or inference:

```bash
.venv/bin/python scripts/m4_repository_codex_prepare.py \
  --contract work/m4-benchmark/runtime-contract.json \
  --output work/m4-benchmark/codex-prepare-new
```

This reuses M0's official-package SHA512-checked installer for CLI 0.160.1. `prepare.json` returns an
`agent_image_id`; pass it to `prepare-task --agent-image` when creating another contract. The verifier
image remains separate. Building and checking the CLI version do not verify real native-session recovery.

The developer entry point `rtv runtime submit --task-contract ...` supports
`fixture/mini-fixture/codex/mini`. Scripted adapters require `--scripted-controls`; real adapters reject
that option. Real Codex/API calls need both submission and worker opt-ins plus credentials, and a
separately approved M4 budget. No model credentials were read this stage. Use the new
[Kubernetes delivery entry point](m4-repository-cluster.md); contracts not bound to the pool are rejected.

## Evidence and remaining work

[Evidence](evidence/m4-repository-runtime-arm64-20261011.json) records all checks and earlier failures.
Raw artifacts under `work/m4-benchmark/repository-3/` include experiment data, events, ledgers,
manifests, restored files and isolated verifier logs. Host-metadata failures remain in history.
The frozen statistical protocol and selection are unchanged by these scripted results.

This stage uses zero API calls, $0 cloud spend and no GPUs. Subsequent task delivery, AMD64 images,
resources and statistical linkage are covered in [Kubernetes repository integration](m4-repository-cluster.md).
Model credentials remain disabled by default; the subsequent [credential path and pilot launcher](m4-model-pilot.md) pass fake-credential checks and real dual-agent workflow acceptance on one repository.
Relay effectiveness is not established. The other three development environments remain to be checked.
