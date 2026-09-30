"""Build a source-only ZIP that starts with `docker compose up -d --build` once unpacked (after creating .env).

Secret material is rejected before the archive is written. Local-only evaluation data is never packed.
"""
import argparse
import re
from collections.abc import Iterator
from fnmatch import fnmatch
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parents[1]
# Everything the Dockerfiles and compose.yaml need (alembic.ini, migrations/, docker/), plus tests and docs.
DIRECTORIES = {'app', 'tests', 'scripts', 'sample_data', 'evaluation', 'frontend', 'migrations', 'docker', 'docs'}
ROOT_FILES = {'.env.example', '.gitattributes', '.gitignore', '.dockerignore', 'Dockerfile', 'compose.yaml',
              'alembic.ini', 'pytest.ini', 'requirements.txt', 'requirements.lock.txt'}
EXCLUDED = {'node_modules', 'dist', '.git', '.venv', '.cache', '__pycache__', '.pytest_cache'}
# The user's own documents, questions and answers (kept local, as in .gitignore / .dockerignore).
PRIVATE = ('evaluation/custom/*', 'evaluation/results*.json')


def submission_files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob('*')):
        rel = path.relative_to(root)
        if not path.is_file() or path.is_symlink() or any(part in EXCLUDED for part in rel.parts):
            continue
        if any(fnmatch(rel.as_posix(), pattern) for pattern in PRIVATE):
            continue
        if any(part.startswith('.env') and part != '.env.example' for part in rel.parts):
            continue
        if path.suffix.lower() in {'.pem', '.key', '.p12', '.pfx', '.zip', '.log', '.pyc', '.xml'}:
            continue
        if any(word in path.name.lower() for word in ('credential', 'secret', 'token.json')):
            continue
        if len(rel.parts) == 1:
            if path.name not in ROOT_FILES and path.suffix != '.md':
                continue
        elif rel.parts[0] not in DIRECTORIES:
            continue
        yield path


def package(root: Path, output: Path) -> int:
    keys = []
    for env in (root / '.env', root / 'frontend' / '.env'):
        if env.is_file():
            for line in env.read_text(encoding='utf-8').splitlines():
                name, sep, value = line.partition('=')
                if sep and any(word in name.upper() for word in ('KEY', 'SECRET', 'TOKEN', 'PASSWORD')):
                    value = value.strip().strip('\"\'')
                    if len(value) >= 8 and value != 'your_api_key':
                        keys.append(value.encode())
    files = list(submission_files(root))
    for path in files:
        content = path.read_bytes()
        if any(key in content for key in keys) or re.search(rb'AIza[0-9A-Za-z_-]{30,}', content):
            raise ValueError(f'Secret detected in {path.relative_to(root)}; archive not created.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, 'w', ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    with ZipFile(output) as archive:
        if archive.testzip() is not None:
            raise ValueError('Archive integrity check failed.')
    return len(files)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'submission' / 'bai1-rag.zip')
    args = parser.parse_args()
    count = package(ROOT, args.output)
    print(f'Created {args.output} ({count} files); secret and integrity checks passed.')
