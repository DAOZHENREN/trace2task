import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts.local_gui import download as downloader


def _manifest(tmp_path, *, repo=None, revision=None, filename="config.json"):
    model = "qwen3-vl-2b"
    expected_repo, expected_revision = downloader.REPOS[model]
    content = b'{}'
    folder = tmp_path / model
    folder.mkdir()
    metadata = {"sha": revision or expected_revision, "siblings": [{
        "rfilename": filename, "size": len(content),
        "blobId": hashlib.sha1(b"blob 2\0" + content).hexdigest(),
    }]}
    (folder / "download-manifest.json").write_text(json.dumps({
        "repo": repo or expected_repo, "metadata": metadata,
    }), encoding="utf-8")
    return folder


@pytest.mark.parametrize("changes, message", [
    ({"repo": "other/model"}, "repository mismatch"),
    ({"revision": "not-pinned"}, "revision mismatch"),
    ({"filename": "..\\config.json"}, "Unsafe download filename"),
    ({"filename": "../config.json"}, "Unsafe download filename"),
    ({"filename": "C:config.json"}, "Unsafe download filename"),
])
def test_rejects_mismatched_or_unsafe_cached_manifest(tmp_path, monkeypatch, changes, message):
    folder = _manifest(tmp_path, **changes)
    def unexpected(*args):
        pytest.fail("Invalid metadata must fail before downloading files")
    monkeypatch.setattr(downloader, "curl", unexpected)
    with pytest.raises(ValueError, match=message):
        downloader.download(tmp_path, "qwen3-vl-2b", "https://example.invalid")
    assert not (folder / "verified.json").exists()


def test_verifies_download_before_replacing_existing_file(tmp_path, monkeypatch):
    folder = _manifest(tmp_path)
    (folder / "config.json").write_bytes(b"previous")
    def corrupt_response(url, *args):
        output = Path(args[args.index("--output") + 1])
        output.write_bytes(b"corrupt")
        return SimpleNamespace(stdout=b"")
    monkeypatch.setattr(downloader, "curl", corrupt_response)
    with pytest.raises(RuntimeError, match="Checksum mismatch"):
        downloader.download(tmp_path, "qwen3-vl-2b", "https://example.invalid")
    assert (folder / "config.json").read_bytes() == b"previous"
    assert not (folder / "verified.json").exists()


def test_accepts_matching_pinned_cached_file(tmp_path, monkeypatch):
    folder = _manifest(tmp_path)
    (folder / "config.json").write_bytes(b"{}")
    def unexpected(*args):
        pytest.fail("Verified cached file should not be downloaded")
    monkeypatch.setattr(downloader, "curl", unexpected)
    downloader.download(tmp_path, "qwen3-vl-2b", "https://example.invalid")
    result = json.loads((folder / "verified.json").read_text(encoding="utf-8"))
    assert result["revision"] == downloader.REPOS["qwen3-vl-2b"][1]
    assert result["files"][0]["sha256"] == hashlib.sha256(b"{}").hexdigest()
