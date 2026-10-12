"""Explicit cluster configuration; never select the user's current kube context."""

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from rtv.runtime.config import Settings


@dataclass(frozen=True)
class ClusterConfig:
    kubeconfig: str
    context: str
    namespace: str
    shared_path: str
    runner_image: str
    images_archive: str
    images_sha256: str
    pod_database_url: str
    control_database_url: str
    max_jobs: int = 2
    cpu_millis: int = 1000
    memory_mb: int = 1536
    engine_disk_gib: int = 4
    image_load_seconds: int = 90
    repository_contract_sha256: str | None = None

    def __post_init__(self):
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,61}[a-z0-9]", self.namespace):
            raise ValueError("Invalid dedicated namespace")
        if not self.context or not Path(self.kubeconfig).is_absolute():
            raise ValueError("Explicit kubeconfig and context required")
        shared = Path(self.shared_path)
        archive = Path(self.images_archive)
        if not shared.is_absolute() or not archive.resolve().is_relative_to(shared.resolve()):
            raise ValueError("Image archive must be within the absolute shared volume")
        if not re.fullmatch(r"[0-9a-f]{64}", self.images_sha256):
            raise ValueError("Image archive SHA256 required")
        if not re.fullmatch(r"[\w./:-]+@sha256:[0-9a-f]{64}", self.runner_image):
            raise ValueError("Runner image must use an immutable digest")
        if not 1 <= self.max_jobs <= 8 or not 250 <= self.cpu_millis <= 8000:
            raise ValueError("Invalid bounded job/CPU capacity")
        if not 512 <= self.memory_mb <= 16384:
            raise ValueError("Invalid job memory capacity")
        if not 4 <= self.engine_disk_gib <= 64 or not 30 <= self.image_load_seconds <= 600:
            raise ValueError("Invalid executor disk or image-load envelope")
        if self.repository_contract_sha256 is not None and (
            not re.fullmatch(r"[0-9a-f]{64}", self.repository_contract_sha256)
            or self.memory_mb < 5120
            or self.cpu_millis < 2000
            or self.engine_disk_gib < 16
        ):
            raise ValueError(
                "Repository pool requires a pinned contract, 2 CPU, 5 GiB and 16 GiB disk"
            )

    @classmethod
    def load(cls):
        return cls(**json.loads(Path(os.environ["RTV_CLUSTER_CONFIG"]).read_text()))

    def validate_root(self, settings):
        if settings.schema != "rtv4" or not settings.root.resolve().is_relative_to(
            Path(self.shared_path).resolve()
        ):
            raise ValueError("Kubernetes execution requires rtv4 and a root on the shared volume")

    def frozen(self):
        # Credentials and the host kubeconfig path are not part of an experiment.
        return {
            k: v
            for k, v in asdict(self).items()
            if k not in {"kubeconfig", "pod_database_url", "control_database_url"}
        }


def pool_label(settings: Settings):
    return hashlib.sha256(str(settings.root).encode()).hexdigest()[:24]
