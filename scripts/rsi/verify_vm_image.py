"""Admit the locked official VM archive, without implicit main-branch downloads."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path, PurePosixPath

EXPECTED_SIZE = 14_189_763_267
EXPECTED_SHA = "eb737ae70b49849e24af407de6a518439a23de05a8497096a948334ce0a909aa"
IMAGE_NAME = "osworld-v2-ubuntu-x86.qcow2"
# The release tag currently resolves to different bytes. This historical
# immutable commit still contains the exact artifact in RSI's release lock.
ARTIFACT_COMMIT = "8213366932c553e5fe758d0f2c8c8b81ffc3be8c"


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    archive = root / "downloads/osworld-v2-ubuntu-x86.qcow2.zip.part"
    destination = root / "OSWorld-V2/docker_vm_data"
    receipt = root / "downloads/vm-verified.json"
    if archive.is_symlink() or archive.stat().st_size != EXPECTED_SIZE:
        raise ValueError("Archive is incomplete or does not match the locked size")
    archive_sha = digest(archive)
    if archive_sha != EXPECTED_SHA:
        raise ValueError("Official VM archive SHA-256 mismatch; refusing extraction")
    destination.mkdir(exist_ok=True)
    if not destination.resolve().is_relative_to(root) or destination.is_symlink():
        raise ValueError("Unsafe VM destination")
    target = destination / IMAGE_NAME
    if target.exists() or receipt.exists():
        raise FileExistsError("Existing VM or receipt will not be overwritten")
    temporary = destination / (IMAGE_NAME + ".verifying")
    with zipfile.ZipFile(archive) as bundle:
        matches = [entry for entry in bundle.infolist()
                   if PurePosixPath(entry.filename).name == IMAGE_NAME and not entry.is_dir()]
        if len(matches) != 1:
            raise ValueError("Expected exactly one official QCOW2 image")
        entry = matches[0]
        if (PurePosixPath(entry.filename).is_absolute()
                or ".." in PurePosixPath(entry.filename).parts
                or entry.file_size > 128 * 1024**3):
            raise ValueError("Unsafe archive image entry")
        with bundle.open(entry) as source, temporary.open("xb") as output:
            shutil.copyfileobj(source, output, length=8 * 1024 * 1024)
            output.flush()
            os.fsync(output.fileno())
    image_sha = digest(temporary)
    temporary.rename(target)
    payload = {"archive_sha256": archive_sha, "archive_size": EXPECTED_SIZE,
               "image_sha256": image_sha, "image_size": target.stat().st_size,
               "image_mtime_ns": target.stat().st_mtime_ns,
               "source": f"xlangai/v2-image@{ARTIFACT_COMMIT}",
               "release_lock_tag": "v2026.06.24"}
    with receipt.open("x", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    print(json.dumps(payload, indent=2))


if __name__ == "__main__":
    main()
