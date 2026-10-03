"""Convert the verified GUI-Owl checkpoint using an explicitly supplied llama.cpp checkout.

Never downloads code, replaces existing GGUFs, or changes the original checkpoint.
--record-existing is for outputs already produced by that exact converter/revision;
the caller asserts provenance, while this script verifies inputs and records hashes.
"""
import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))
from trace2task.model_registry import MODEL_PROFILES, profile_for


def sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('D:/Models/Trace2Task-GUI'))
    parser.add_argument('--llama-source', type=Path, required=True)
    parser.add_argument('--record-existing', action='store_true')
    parser.add_argument('--model', choices=[p.id for p in MODEL_PROFILES.values()
                                            if 'llama-server' in p.engines and not p.prebuilt_gguf], default='gui-owl-2b')
    args = parser.parse_args()
    profile = profile_for(args.model, 'llama-server')
    source = args.root / profile.id
    report = json.loads((source / 'verified.json').read_text(encoding='utf-8'))
    if report['revision'] != profile.revision:
        raise ValueError('Unexpected checkpoint revision for ' + profile.id)
    for item in report['files']:
        path = (source / item['file']).resolve()
        if not path.is_relative_to(source.resolve()) or sha256(path) != item['sha256']:
            raise ValueError('Original checkpoint checksum mismatch: ' + item['file'])
    converter = args.llama_source / 'convert_hf_to_gguf.py'
    revision = subprocess.check_output(['git', '-C', str(args.llama_source), 'rev-parse', 'HEAD'], text=True).strip()
    output = args.root / 'gguf' / profile.id
    output.mkdir(parents=True, exist_ok=True)
    variants = [('model-BF16.gguf', ['--outtype', 'bf16', '--model-name', profile.repository.split('/')[-1]]),
                ('mmproj-F16.gguf', ['--outtype', 'f16', '--mmproj'])]
    if not args.record_existing and any((output / name).exists() for name, _ in variants):
        raise FileExistsError('GGUF output exists; it will not be overwritten. See --record-existing help.')
    commands, files = [], []
    for name, options in variants:
        path = output / name
        command = [sys.executable, str(converter), str(source), '--outfile', str(path), *options]
        commands.append(command)
        if not args.record_existing:
            subprocess.run(command, check=True)
        with path.open('rb') as stream:
            if stream.read(4) != b'GGUF':
                raise ValueError('Not a GGUF file: ' + str(path))
        files.append({'file': name, 'sha256': sha256(path), 'size': path.stat().st_size})
    manifest = {'model': profile.repository, 'revision': report['revision'],
                'llama_cpp_revision': revision, 'converter_sha256': sha256(converter),
                'original_verified_sha256': sha256(source / 'verified.json'), 'reproduction_commands': commands,
                'recorded_existing_outputs': args.record_existing, 'files': files}
    (output / 'verified-gguf.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(output / 'verified-gguf.json')


if __name__ == '__main__':
    main()
