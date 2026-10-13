import json
import tarfile
from uuid import uuid4

import pytest

from rtv.benchmark.archive import archive_completed


def test_completed_archive_retains_bytes_permissions_and_links(tmp_path):
    trial = tmp_path / str(uuid4())
    trial.mkdir()
    script = trial / "script"
    script.write_bytes(b"saved workspace\x00data")
    script.chmod(0o751)
    (trial / "link").symlink_to("script")
    (tmp_path / "observations").mkdir()
    (tmp_path / "report.json").write_text(
        json.dumps({"checks": {"all_allocations_released": True}})
    )
    receipt = archive_completed(tmp_path)
    assert receipt["readback_verified"] and not trial.exists()
    assert (tmp_path / "observations").exists()
    with tarfile.open(tmp_path / receipt["archive"], "r:gz") as tar:
        assert tar.extractfile(trial.name + "/script").read() == b"saved workspace\x00data"
        assert tar.getmember(trial.name + "/script").mode == 0o751
        assert tar.getmember(trial.name + "/link").linkname == "script"
    assert archive_completed(tmp_path) == receipt


def test_archive_refuses_active_allocations(tmp_path):
    (tmp_path / "report.json").write_text('{"checks":{"all_allocations_released":false}}')
    with pytest.raises(ValueError, match="unreleased"):
        archive_completed(tmp_path)
