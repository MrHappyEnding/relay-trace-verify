# RTV Framework Development Guide

[![中文](assets/lang-zh.svg)](framework.md) [![English](assets/lang-en.svg)](framework.en.md)

Status: local framework and M0 integration preparation · 2026-10-08

## 1. Current code

```text
src/rtv/
  domain.py        # Trial states, execution identities, handoff context, capabilities
  contracts.py     # AgentAdapter / WorkspaceBackend / CheckpointStore / EventSink / Verifier
  checkpoints.py   # Local manifest v1, digest references, and format validation
  storage.py       # File checkpoints and independent working directories
  events.py        # Single-writer JSONL events
  runner.py        # Two-stage local relay composed from the interfaces
  demo.py          # Deterministic fixture agents and verifier
  harbor_probe.py  # Optional Harbor preflight and container file recovery prototype
  cli.py           # demo / harbor-doctor / harbor-smoke / --version
tests/
  test_domain.py
  test_checkpoints.py
  test_relay.py
  test_harbor_probe.py
examples/tasks/positive-sum/  # Controlled scripted task in Harbor format
```

The core runtime uses only the Python standard library and supports Python 3.11+. The optional `harbor` group requires Python 3.12+; Harbor is imported lazily by the integration entry points. pytest and Ruff are development dependencies, with their versions pinned in `uv.lock`. `pyproject.toml` defines the installable package and CLI.

## 2. Run and check

```bash
uv sync --locked --dev
uv run rtv --version
uv run rtv demo --output work/demo
uv run pytest
uv run ruff check src tests
uv run ruff format --check src tests
```

`rtv demo` requires a new output directory and never overwrites an existing experiment. Successful execution returns exit code 0; verification failures or runtime errors return a nonzero code. The demo does not download tasks, call models, or start containers.

Fixture A writes 41 to `answer.txt` and requests a handoff. B reads the restored file in another execution directory and changes it to 42. A separate verifier checks its contents. This verifier is separate from the agent interface but runs in the same host process; it is not an isolated hidden-test service.

See the [M0 guide](m0.en.md) for Harbor installation, `harbor-doctor`, and `harbor-smoke`. The container prototype is separate from the synchronous Runner: it validates vendor interfaces and file transfer before defining formal adapter contracts. It is not a completed WorkspaceBackend.

## 3. Artifacts and identities

```text
work/demo/
  events.jsonl
  report.json
  checkpoints/<checkpoint-uuid>/
    manifest.json
    files/
  workspaces/<source-execution-uuid>/
  workspaces/<target-execution-uuid>/
```

Events retain Trial, Attempt, Execution, epoch, and source sequence identifiers. Handoff keeps the Trial ID and creates a new Attempt/Execution. `ExecutionRef.recover()` only creates a new execution identity and next epoch for the same Attempt; it does not acquire leases, recover processes, or fence stale executions.

`report.json` uses `mode: local-fixture` and records source/target workspaces, the checkpoint ID, manifest SHA-256, final status, and verification result.

## 4. Local checkpoint guarantees

- The caller must stop all writes first. Fixtures are synchronous functions with no background processes after return.
- Write all files and the manifest into a temporary directory, then publish with a directory rename on the same filesystem.
- Only published UUID directories can be loaded by reference. Loading validates the manifest digest.
- Restore validates each file's size and SHA-256, publishing the destination only after it has been fully written.
- Preserve regular-file contents, file permission bits, and empty directories. Reject symbolic links, special files, path traversal, and file/directory conflicts.
- Reject existing restore destinations. Branches copy files and share no writable contents.

Local format v1 stores files, a summary, and an event boundary. It does not yet include native sessions, environment reconstruction manifests, or a budget ledger. It is a starting point for the full checkpoint protocol in the system design.

The store assumes a controlled single-process environment, not a security boundary against hostile disk writes. Concurrent source modifications, cross-process commits, power-loss durability, reference garbage collection, and distributed ownership are unsupported. SHA-256 provides integrity checking, not signatures.

## 5. Integration points

| Interface | Current implementation | Next step |
| --- | --- | --- |
| AgentAdapter | PrepareAgent / FinishAgent | M0 verification of real CLI stage termination, session formats, and quiescence |
| WorkspaceBackend | Ordinary local directories | Validate container and remote-workspace contracts against Harbor environment interfaces |
| CheckpointStore | Local files and manifests | Add sessions, environment manifests, object storage, and conditional commits |
| EventSink | Single-writer JSONL | Durable identities, deduplication, reconnection, and query projections |
| Verifier | Fixed file-content check | Harbor task verifiers and isolated verification environments |
| Runner | Synchronous two-stage function | Temporal orchestration, cancellation, recovery, and resource cleanup |

These are provisional, changeable contracts. Do not consider them stable APIs before the Harbor compatibility prototype is complete.

## 6. Validation scope

Tests cover recovery/handoff identity differences, state transitions, cancellation state constraints, file and branch isolation, manifest/content corruption, missing files, unsafe paths, symbolic links, existing-destination protection, and commit write failures. End-to-end tests also cover event correlation, final status after verifier failure, and CLI behavior.

Optional Harbor tests use the real Task parser with mocked container transport, covering failed stops, round-trip corruption, failed verification, and cleanup leaks. The real-container integration test is skipped unless `RTV_RUN_DOCKER=1` is set; mocked tests are not evidence of container validation.

A separately executed real Docker smoke verified file recovery between two containers, workspace isolation, and the Harbor verifier; see the [M0 guide](m0.en.md). This evidence does not establish real-agent handoff gains, general sandbox security, automatic workflow recovery after failure, budget caps, or multi-worker correctness. Validate those incrementally through the [roadmap](roadmap.en.md).
