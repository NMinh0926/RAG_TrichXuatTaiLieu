from zipfile import ZipFile

import pytest
from scripts.package_submission import package


def test_submission_excludes_runtime_and_secrets(tmp_path):
    for name in ['.env', 'frontend/.env', 'frontend/.env.local', 'frontend/node_modules/x.js',
                 'frontend/dist/x.js', 'data/uploads/private.pdf', 'data/chroma/db',
                 '.cache/x', 'app/__pycache__/x.pyc', 'scripts/private.key']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('private', encoding='utf-8')
    for name in ['.env.example', 'frontend/.env.example', 'app/main.py', 'README.md',
                 'frontend/package-lock.json', 'sample_data/sample.pdf', 'evaluation/questions.json']:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('public', encoding='utf-8')
    archive = tmp_path / 'submission/final.zip'
    assert package(tmp_path, archive) == 7
    with ZipFile(archive) as result:
        assert len(result.namelist()) == 7
        assert 'frontend/.env.example' in result.namelist()
        assert 'data/uploads/private.pdf' not in result.namelist()


def test_submission_refuses_literal_secret_without_echoing_it(tmp_path):
    secret = 'synthetic-private-key-123456'
    (tmp_path / '.env').write_text('PRIVATE_TOKEN=' + secret)
    (tmp_path / 'README.md').write_text('accidental ' + secret)
    output = tmp_path / 'final.zip'
    with pytest.raises(ValueError) as caught:
        package(tmp_path, output)
    assert secret not in str(caught.value)
    assert not output.exists()
