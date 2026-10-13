# ADR 0005: Kubernetes execution identity, reservations and recovery

Status: M4 phase one implemented; local acceptance scope is in the [M4 guide](../m4.md).

## Problem and prior art

The M3 host supervisor and file locks cannot directly coordinate execution across nodes.
A control worker restart, lost Kubernetes API acknowledgement and lost execution node are distinct
failures. Retrying launches can duplicate agent work; releasing capacity on lease expiry can admit
new work while an old process still exists.

We reuse [Kubernetes Jobs](https://kubernetes.io/docs/concepts/workloads/controllers/job/) and
[resource quotas](https://kubernetes.io/docs/concepts/policy/resource-quotas/),
[Temporal Activities](https://docs.temporal.io/activity-definition) for retryable control, and
[PostgreSQL advisory locks](https://www.postgresql.org/docs/17/explicit-locking.html#ADVISORY-LOCKS)
for coordination. Harbor 0.24.0 DockerEnvironment and the DinD pattern preserve the existing sandbox
and verifier integration; M0–M3 contract tests cover the pinned execute/stop/export interfaces.
These are upstream capabilities. RTV adapts execution identity, reservations, checkpoint/epoch
ownership and Job reconciliation, with fault evidence for that composition.

## Decisions

1. `rtv4` has a separate PostgreSQL schema, Temporal queue and Workflow ID prefix. M3 defaults remain.
   Each physical Execution has one stable Job name. Each Job starts a private Docker daemon and
   supervisor; Harbor manages its inner containers. Kubernetes owns Job/Pod lifecycle; RTV sends
   idempotent create/delete intent.
2. Job identity, epoch, task specification, pinned image, absolute deadline and excluded recovery node
   form a contract digest. Lost creation acknowledgement leads to lookup/adoption; mismatched contracts
   are rejected. Deletion uses a UID precondition. `backoffLimit=0` and `restartPolicy=Never` accompany
   PostgreSQL claim/call admission; Kubernetes Jobs alone do not guarantee exactly-once model execution.
3. A runtime root defines a pool. PostgreSQL transactions serialize jobs/CPU/memory reservations;
   advisory locks coordinate control workers. Lease expiry does not release capacity. Both Job and
   Pods must be observed absent before release. Namespace ResourceQuota bounds physical resources too.
4. Recovery creates a fresh Execution, Job and writable directory, preserving Attempt and absolute TTL.
   Only committed, validated checkpoints qualify. The new reservation freezes the preceding node as
   an exclusion, including the window before Kubernetes recognizes node failure. Old capacity remains
   held until normal deletion acknowledgement; unreachable Pods are never force-deleted automatically.
   Recovery can therefore wait when capacity is exhausted.
5. A stale epoch cannot publish results/checkpoints or admit new calls. Already-issued external effects
   cannot be revoked. Interrupted calls remain UNKNOWN; recovery neither prices them at zero nor settles
   them automatically. Restored workspace contents are checked again.
6. Image digests, dependency lock and image archive SHA256 are frozen. Archive validation precedes daemon
   startup. Host and inner Docker use the containerd image store to preserve image identity across import;
   see [Docker image store](https://docs.docker.com/engine/storage/containerd/).
7. The phase-one runner accepts only `fixture` / `mini-fixture`, without API credential delivery or a
   real-model entry point. Kubeconfig stays outside the shared volume. Jobs mount neither the host Docker
   socket nor a service account token. Supervisor logs and task/recovery artifacts outlive Job cleanup.

## Deliberate limitations

This is one Docker Desktop host with k3d: one control node, two execution nodes and a shared host bind
volume. It validates coordination and recovery across Kubernetes node processes, not independent-machine
failure, host power loss, PostgreSQL HA or object-store durability. Temporal and control workers run on
the host.

The outer DinD Pod is privileged. This is a trusted local development deployment, not a multi-tenant
security boundary. Metadata uses a fixed local development credential. Production requires Secrets,
access/network isolation, durable shared storage, database HA and image distribution.

Pool reservations are per root; namespace quota applies globally. Fair scheduling is not promised.
The repair loop scans the root's history and needs cursor/archive work before large-scale use.
Reports distinguish fault injection, epoch transfer, claim and workspace restoration. Statistics describe
only the observed sample. Frozen formal tasks, real-model paired experiments, confidence intervals and
strategy-effect conclusions remain later M4 work.
