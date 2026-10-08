"""Install official pinned native Codex executables without npm or install scripts."""

import base64
import hashlib
import platform
import tarfile
import tempfile
import urllib.request
from pathlib import Path

VERSION = "0.160.1"
PACKAGES = {
    "aarch64": (
        "arm64",
        "aarch64-unknown-linux-musl",
        "JLyjBlmjPvwTaicHemw+y5xQSz3Uja2r7F/xkvS8gFufTA2g132Ak+0fo35xnJ/k2uc9fTtjm0LrsEGI78L6Ng==",
    ),
    "x86_64": (
        "x64",
        "x86_64-unknown-linux-musl",
        "sIDhqV+bsZKKVaVFVY5iB+pAzyOz2XRm3H1KXCsSJwGj5p98qnrT0Fweo1Hfe7WnyTp8mfBNdbVzUeO+mHYugA==",
    ),
}


def main():
    arch, triple, integrity = PACKAGES[platform.machine()]
    url = f"https://registry.npmjs.org/@openai/codex/-/codex-{VERSION}-linux-{arch}.tgz"
    with tempfile.TemporaryDirectory() as directory:
        archive = Path(directory) / "codex.tgz"
        urllib.request.urlretrieve(url, archive)
        digest = hashlib.file_digest(archive.open("rb"), "sha512").digest()
        if digest != base64.b64decode(integrity):
            raise ValueError("Official package integrity check failed")
        with tarfile.open(archive) as tar:
            # Preserve relative runtime resource paths. Voice assets are not used.
            for relative in (
                "bin/codex",
                "bin/codex-code-mode-host",
                "codex-resources/bwrap",
                "codex-path/rg",
                "codex-resources/zsh/bin/zsh",
                "codex-package.json",
            ):
                member = tar.getmember(f"package/vendor/{triple}/{relative}")
                if not member.isfile():
                    raise ValueError("Unexpected non-regular package entry")
                destination = Path("/opt/codex") / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                with tar.extractfile(member) as source, destination.open("wb") as target:
                    while chunk := source.read(1024 * 1024):
                        target.write(chunk)
                destination.chmod(member.mode & 0o777)
    Path("/usr/local/bin/codex").symlink_to("/opt/codex/bin/codex")
    Path("/usr/local/bin/rg").symlink_to("/opt/codex/codex-path/rg")


if __name__ == "__main__":
    main()
