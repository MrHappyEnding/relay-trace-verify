"""Download a pinned official dev-server CLI into ignored work/tools."""

import hashlib
import platform
import tarfile
import urllib.request
from pathlib import Path

VERSION = "1.9.1"
system = platform.system().lower()
arch = {"arm64": "arm64", "aarch64": "arm64", "x86_64": "amd64"}.get(platform.machine())
if system not in {"darwin", "linux"} or arch is None:
    raise SystemExit("M1 currently supports macOS/Linux on ARM64 or AMD64")
root = Path(__file__).resolve().parents[1] / "work/tools"
root.mkdir(parents=True, exist_ok=True)
name = f"temporal_cli_{VERSION}_{system}_{arch}.tar.gz"
url = f"https://github.com/temporalio/cli/releases/download/v{VERSION}/"
checksums = urllib.request.urlopen(url + "checksums.txt").read().decode()
expected = next(line.split()[0] for line in checksums.splitlines() if line.endswith(name))
archive = root / name
urllib.request.urlretrieve(url + name, archive)
actual = hashlib.sha256(archive.read_bytes()).hexdigest()
if actual != expected:
    raise SystemExit("Temporal archive SHA256 mismatch")
with tarfile.open(archive) as package:
    package.extract("temporal", path=root, filter="data")
print(f"Verified Temporal CLI {VERSION}: {actual}\n{root / 'temporal'}")
