# M4: Model credentials and the first real repository pilot

[![中文](assets/lang-zh.svg)](m4-model-pilot.md) [![English](assets/lang-en.svg)](m4-model-pilot.en.md)

**Status: credential delivery and the real-model pilot launcher are implemented; 17 local Kubernetes
fake-credential checks pass. Real-model execution awaits approval for this run.** No real credentials
were read or uploaded, no models were called, and no GPUs were used. The
[stage evidence](evidence/m4-credentials-arm64-20261012.json) separates mocked tests, real-cluster
fake-credential checks and pending work. Real authentication, native Codex `/testbed` continuation
and model effectiveness on SWE-bench remain unverified.

## Credentials and execution admission

The default pool has no `model_access` and still rejects real models. Only the explicitly approved
launcher creates two immutable Kubernetes Secrets and records **name + UID references** in that run's
configuration. Metadata contains neither credential contents nor their hashes. Secret values go
directly through the Kubernetes API, without shell arguments, shared-volume staging or Workflow
history. Launcher reports keep filtered types rather than raw authentication responses.

- Admission binds one runtime root, frozen run SHA, five Trial IDs, their spec hashes and request
  allowances. Controller and runner both validate these; only epoch 1 is accepted, with no automatic
  recovery or replay of model stages.
- Each Job mounts exactly one Secret for its adapter: read-only projection, `0400` file, no
  service-account token. Inherited model authentication variables are cleared before spawning the
  supervisor; only the admitted adapter receives opt-in and a file path.
- Mini's API key stays in the outer executor; Harbor executes tools in the network-disabled inner
  container. The existing Harbor Codex adapter copies its login cache into the inner temporary auth
  directory. Checkpoints export only the allowed files.
- A lost Secret-create acknowledgement is recovered by reading and checking the same immutable
  object, owner and contents, without a second create. Job admission checks Secret UID, and cleanup
  uses a UID precondition rather than deleting a replacement object.

Both nodes share one Docker Desktop host. Production executor Pods still use privileged DinD and
support trusted local experiments only. Secret volumes cannot pin object UIDs themselves; the
pre-launch check assumes trusted namespace administrators. This does not defend against cluster
administrators, hostile privileged processes or an agent reading its own auth file. Secret rotation,
persisting refreshed Codex credentials, multi-host secret management and encryption-at-rest setup
are not implemented. If the launcher crashes, use `credential-intent.json` and
`credential-references.json` to identify and clean up this run's Secrets; never bulk-delete unrelated
namespace credentials.

## Frozen experiment and cost

The first run covers only `sympy__sympy-22914`, preserving the existing protocol, images, hidden-test
separation and four-arm order. Agents receive the public issue and selected handoff context, without
scripted repairs or the reference solution. Preparation does not access real credentials.

| Stage | Configuration | Limit |
| --- | --- | --- |
| Shared prefix | Codex `0.160.1` / `gpt-6-astra` | 300 seconds; sealed without hidden verification |
| A-continue | Same native Codex session | 300 seconds |
| B-workspace | mini-swe-agent `2.4.6` / `gpt-5.4-mini-2026-03-17` | 300 seconds / 8 requests |
| B-summary | Same, with an unverified summary | 300 seconds / 8 requests |
| B-fresh | Same, starting from the original repository | 600 seconds / 16 requests |

Each Trial has a total 1800-second deadline including queueing, preparation and verification; a pool
runs one Job at a time. Source failures are retained and the fresh baseline still runs in its frozen
order. Infrastructure failures do not trigger automatic reruns. Real and oracle observations are
exported separately. Collection checks actual specs, context policies, operation types, checkpoints
and prefix accounting. Incomplete verification is not a graded outcome. An honest unsolved model
result remains an observation; the launcher does not retry until the repair succeeds.

The [official OpenAI pricing](https://developers.openai.com/api/docs/models/gpt-5.4-mini), checked on
2026-10-12, lists $0.75 input, $0.075 cached input and $4.50 output per million tokens. This transport
allows 40,000 input bytes and 4,096 output tokens per request, reserving a non-reusable $0.062.
The 32 requests reserve **$1.984**, with a proposed **$2 API approval** for this run. This is local
admission accounting, not a provider invoice guarantee. Two Codex stages additionally consume
existing subscription allowance; their dollar cost stays unknown rather than free. No cloud
resources are provisioned. Authentication follows the
[official OpenAI authentication documentation](https://learn.chatgpt.com/docs/auth); M3's API transport
and Codex adapter are reused without a model upgrade.

## Usage

First prepare the pinned Codex CLI agent image/contract using the
[local repository guide](m4-repositories.en.md), then deliver a package with `cluster prepare-repository`.
It contains that agent image, the independent verifier and network sidecar. Keep the local PostgreSQL
port-forward and Temporal service running. Package and runtime paths must be on the cluster volume.

```bash
# No model calls or credentials: freeze a plan using fresh plan and runtime paths.
.venv/bin/python scripts/m4_model_pilot.py prepare \
  --config work/m4-data/model-repository-package-1/config.json \
  --contract work/m4-data/model-repository-package-1/contract.json \
  --root work/m4-data/real-model-pilot-1 \
  --output work/m4-control/model-pilot-prepared-1.json

# Only after owner approval. Credentials must be private regular files outside the shared volume.
.venv/bin/python scripts/m4_model_pilot.py run \
  --prepared work/m4-control/model-pilot-prepared-1.json \
  --auth-file "$HOME/.codex/auth.json" \
  --api-key-file work/openai-api-key.txt \
  --allow-model-calls --allow-api-calls --approved-budget-usd 2
```

Execution exports `run.json`, ledgers, observations and checks, then cleans up owned Jobs, Pods and
Secrets. `benchmark collect-run` can re-export observations without launching anything. Failed
artifacts remain in ignored `work/`; a new attempt needs a new plan and a remaining-budget review.

The fake-credential acceptance script, `scripts/m4_credentials_acceptance.py`, runs a replacement
entrypoint on both nodes to check Secret delivery, permissions, adapter-specific variables, no model
client import, log leakage and cleanup. It does not run the production supervisor or a model. The
first attempt encountered the Kubernetes Python client's JSON-log-to-dictionary-to-string conversion;
reading raw log bytes fixed it. That failed attempt is retained.

Remaining: real session/handoff acceptance, three more development environments, the evaluation set,
and the complete report and demonstration.
