"""Pinned HF mirror downloads. NEVER use environment/system proxies."""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from trace2task.model_registry import MODEL_PROFILES

REPOS = {p.id: (p.repository, p.revision) for p in MODEL_PROFILES.values()}

def curl(url, *args):
    return subprocess.run(['curl.exe', '--noproxy', '*', '--ssl-revoke-best-effort',
                           '--fail', '--location', '--connect-timeout', '20',
                           '--retry', '3', '--speed-limit', '1024', '--speed-time', '60',
                           '--proto', '=https', '--proto-redir', '=https',
                           *args, url], check=True, capture_output=True)

def digest(path, algorithm='sha256'):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, algorithm).hexdigest()

def valid_file(candidate, item):
    if not candidate.is_file() or candidate.stat().st_size != item['size']:
        return False
    expected = item.get('lfs', {}).get('sha256')
    if expected:
        return digest(candidate) == expected
    blob = hashlib.sha1(f'blob {item["size"]}\0'.encode() + candidate.read_bytes()).hexdigest()
    return blob == item['blobId']


def download(root, key, endpoint):
    repo, revision = REPOS[key]
    preset = MODEL_PROFILES[key].prebuilt_gguf
    folder = root / 'gguf' / key if preset else root / key
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / 'download-manifest.json'
    if manifest.exists():
        saved = json.loads(manifest.read_text(encoding='utf-8'))
        if saved.get('repo') != repo:
            raise ValueError(f'Download manifest repository mismatch: {manifest}')
        metadata = saved['metadata']
    else:
        metadata = json.loads(curl(f'{endpoint}/api/models/{repo}/revision/{revision}?blobs=true',
                                   '--silent', '--max-time', '90').stdout)
    if metadata.get('sha') != revision:
        raise ValueError(f'Download revision mismatch; expected pinned revision {revision}')
    for item in metadata['siblings']:
        name = item['rfilename']
        if not name or '\\' in name or ':' in name or any(
            part in {'', '.', '..'} for part in name.split('/')
        ):
            raise ValueError(f'Unsafe download filename: {name!r}')
    if preset:
        pinned_files = dict(preset.files)
        remote_files = {item['rfilename']: item.get('lfs', {}).get('sha256')
                        for item in metadata['siblings'] if item['rfilename'] in pinned_files}
        if remote_files != pinned_files:
            raise ValueError('Official GGUF file checksum metadata differs from the pinned registry')
    if not manifest.exists():
        manifest.write_text(json.dumps({'repo': repo, 'proxy': False, 'endpoint': endpoint,
                                       'metadata': metadata}, indent=2), encoding='utf-8')
    checked = []
    for item in metadata['siblings']:
        name = item['rfilename']
        if (preset and name not in pinned_files) or (not preset and
                ('/' in name or not name.endswith(('.json', '.jinja', '.txt', '.safetensors', '.md')))):
            continue
        path = folder / name
        if path.is_symlink() or path.with_name(name + '.part').is_symlink():
            raise ValueError(f'Download target must not be a symbolic link: {path}')
        expected = item.get('lfs', {}).get('sha256')
        if not valid_file(path, item):
            if preset and path.exists():
                raise FileExistsError(f'Existing GGUF differs from the pinned model; it will not be overwritten: {path}')
            shared = root / 'qwen3-vl-2b' / name
            # Reuse the already verified fixed-revision Qwen base, not the trained adapter.
            cached = Path('D:/Models/Trace2Task-D-5970/weights') / name
            if key == 'qwen3-vl-2b' and expected and cached.is_file() and digest(cached) == expected:
                shutil.copyfile(cached, path)
            elif not preset and key != 'qwen3-vl-2b' and not name.endswith('.safetensors') and valid_file(shared, item):
                shutil.copyfile(shared, path)
            else:
                partial = path.with_name(name + '.part')
                print(f'DOWNLOAD {key}/{name} {item["size"]} bytes (direct, no proxy)', flush=True)
                url = f'{endpoint}/{repo}/resolve/{metadata["sha"]}/{name}'
                timeout = '7200' if name.endswith(('.safetensors', '.gguf')) else '300'
                try:
                    curl(url, '--max-time', timeout, '--continue-at', '-', '--output', str(partial))
                except subprocess.CalledProcessError as error:
                    if error.returncode != 33:
                        raise
                    # Some mirrors cannot resume ordinary Git blobs. Restart only this
                    # incomplete download; never accept it without the checksum below.
                    curl(url, '--max-time', timeout, '--output', str(partial))
                if not valid_file(partial, item):
                    raise RuntimeError(f'Checksum mismatch: {partial}')
                partial.replace(path)
            if not valid_file(path, item):
                raise RuntimeError(f'Checksum mismatch: {path}')
        checked.append({'file': name, 'sha256': digest(path), 'size': path.stat().st_size})
    (folder / ('verified-gguf.json' if preset else 'verified.json')).write_text(json.dumps({
        'model': repo, 'revision': metadata['sha'], 'files': checked},
                                                   indent=2), encoding='utf-8')
    print(f'VERIFIED {key}: {len(checked)} files, revision {metadata["sha"]}', flush=True)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, default=Path('D:/Models/Trace2Task-GUI'))
    parser.add_argument('--endpoint', default='https://hf-mirror.com')
    parser.add_argument('--models', nargs='+', choices=REPOS, default=list(REPOS))
    args = parser.parse_args()
    for key in args.models:
        download(args.root, key, args.endpoint.rstrip('/'))
