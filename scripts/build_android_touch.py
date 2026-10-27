"""Build the bounded touch helper with a locally supplied official D8/R8 jar.

No download, phone connection or device input. The artifact contains classes.dex
only. Rebuild whenever the Java source changes; verify hashes in tests.
"""
import argparse
import hashlib
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--r8', required=True, type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    folder = root / 'src/trace2task/android_touch'
    source = folder / 'BoundedTouch.java'
    with tempfile.TemporaryDirectory(prefix='t2t-touch-build-') as temporary:
        build = Path(temporary)
        classes, dex = build / 'classes', build / 'dex'
        classes.mkdir()
        dex.mkdir()
        subprocess.run(['javac', '--release', '8', '-encoding', 'UTF-8', '-d', str(classes),
                        str(source)], check=True)
        subprocess.run(['java', '-cp', str(args.r8.resolve()), 'com.android.tools.r8.D8',
                        '--min-api', '29', '--output', str(dex),
                        *(str(path) for path in sorted(classes.rglob('*.class')))], check=True)
        artifact = folder / 'bounded-touch.jar'
        with zipfile.ZipFile(artifact, 'w', zipfile.ZIP_DEFLATED) as archive:
            entry = zipfile.ZipInfo('classes.dex', (2026, 1, 1, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(entry, (dex / 'classes.dex').read_bytes())
        metadata = {'protocol': 'T2T2', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                    'jar_sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
                    'compiler_sha256': hashlib.sha256(args.r8.read_bytes()).hexdigest(),
                    'min_api': 29, 'max_duration_ms': None, 'heartbeat_timeout_ms': 1500}
        (folder / 'build.json').write_text(json.dumps(metadata, indent=2) + '\n', encoding='utf-8')
        print(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
