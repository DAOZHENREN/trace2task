import hashlib
import json
from dataclasses import replace
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


@pytest.fixture
def prebuilt_download(tmp_path, monkeypatch):
    key = 'qwen3-vl-8b-instruct'
    profile = downloader.MODEL_PROFILES[key]
    content = {name: b'GGUF fixture ' + name.encode() for name, _ in profile.prebuilt_gguf.files}
    files = tuple((name, hashlib.sha256(data).hexdigest()) for name, data in content.items())
    monkeypatch.setitem(downloader.MODEL_PROFILES, key, replace(profile,
        prebuilt_gguf=replace(profile.prebuilt_gguf, files=files)))
    metadata = {'sha': profile.revision, 'siblings': [
        {'rfilename': name, 'size': len(content[name]), 'lfs': {'sha256': digest}}
        for name, digest in files] + [{'rfilename': 'unrequested-Q8.gguf', 'size': 999}]}
    folder = tmp_path / 'gguf' / key
    folder.mkdir(parents=True)
    (folder / 'download-manifest.json').write_text(json.dumps({'repo': profile.repository, 'metadata': metadata}))
    downloaded = []

    def curl(url, *args):
        output = Path(args[args.index('--output') + 1])
        name = output.name.removesuffix('.part')
        downloaded.append(name)
        output.write_bytes(content[name])
        return SimpleNamespace(stdout=b'')

    monkeypatch.setattr(downloader, 'curl', curl)
    return key, folder, metadata, downloaded


def test_prebuilt_download_gets_only_pinned_model_and_vision(tmp_path, prebuilt_download):
    key, folder, _, downloaded = prebuilt_download
    downloader.download(tmp_path, key, 'https://example.invalid')
    assert downloaded == [name for name, _ in downloader.MODEL_PROFILES[key].prebuilt_gguf.files]
    assert not (folder / 'unrequested-Q8.gguf').exists()
    manifest = json.loads((folder / 'verified-gguf.json').read_text())
    assert manifest['model'] == downloader.REPOS[key][0]
    assert manifest['revision'] == downloader.REPOS[key][1]
    assert not (tmp_path / key).exists()  # No unwanted HF / safetensors download.
    downloaded.clear()
    downloader.download(tmp_path, key, 'https://example.invalid')
    assert downloaded == []


def test_prebuilt_download_refuses_to_overwrite_existing_weights(tmp_path, prebuilt_download):
    key, folder, metadata, downloaded = prebuilt_download
    original = folder / metadata['siblings'][0]['rfilename']
    original.write_bytes(b'other user weights')
    with pytest.raises(FileExistsError, match='will not be overwritten'):
        downloader.download(tmp_path, key, 'https://example.invalid')
    assert original.read_bytes() == b'other user weights'
    assert downloaded == []


def test_prebuilt_publisher_metadata_must_match_registry(tmp_path, prebuilt_download):
    key, folder, metadata, downloaded = prebuilt_download
    metadata['siblings'][0]['lfs']['sha256'] = 'untrusted'
    (folder / 'download-manifest.json').write_text(json.dumps({'repo': downloader.REPOS[key][0], 'metadata': metadata}))
    with pytest.raises(ValueError, match='pinned registry'):
        downloader.download(tmp_path, key, 'https://example.invalid')
    assert downloaded == []
