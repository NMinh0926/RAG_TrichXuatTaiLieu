"""Export/import every object of the S3 bucket to/from a local directory (backup and restore).

Works against any S3-compatible backend (SeaweedFS, S3, R2). Content types are kept in manifest.json.
  python -m scripts.objects_transfer export --dir /backup/objects
  python -m scripts.objects_transfer import --dir /backup/objects
"""
import argparse
import json
import sys
from pathlib import Path

from app.core.config import get_settings
from app.storage.s3 import S3Storage

MANIFEST = 'manifest.json'


def export_objects(storage: S3Storage, directory: Path) -> int:
    directory.mkdir(parents=True, exist_ok=True)
    root = (directory / 'data').resolve()
    manifest = {}
    for page in storage.client.get_paginator('list_objects_v2').paginate(Bucket=storage.bucket):
        for item in page.get('Contents', []):
            key = item['Key']
            target = directory / 'data' / key
            # Object keys are written as paths: refuse anything escaping the backup directory.
            if not target.resolve().is_relative_to(root):
                raise ValueError(f'unsafe object key: {key!r}')
            target.parent.mkdir(parents=True, exist_ok=True)
            stored = storage.get(key)
            with target.open('wb') as handle:
                for block in stored.iter_bytes():
                    handle.write(block)
            manifest[key] = {'content_type': stored.content_type, 'size': stored.size}
    (directory / MANIFEST).write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
    return len(manifest)


def import_objects(storage: S3Storage, directory: Path) -> int:
    manifest = json.loads((directory / MANIFEST).read_text(encoding='utf-8'))
    for key, info in manifest.items():
        storage.put(key, (directory / 'data' / key).read_bytes(), info['content_type'])
    return len(manifest)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('action', choices=['export', 'import'])
    parser.add_argument('--dir', type=Path, required=True)
    args = parser.parse_args()
    storage = S3Storage.from_settings(get_settings())
    count = (export_objects if args.action == 'export' else import_objects)(storage, args.dir)
    print(f'{args.action}ed {count} objects ({storage.bucket})')
    return 0


if __name__ == '__main__':
    sys.exit(main())
