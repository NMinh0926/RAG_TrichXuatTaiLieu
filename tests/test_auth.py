"""Accounts, sessions, per-user document visibility/sharing, rate limits, health and status endpoints."""
import json
import logging
import uuid
from types import SimpleNamespace

import fitz
import pytest
from fastapi.testclient import TestClient

from app.api.questions import get_readable_document_ids
from app.core.auth import COOKIE_NAME, get_current_user
from app.core.config import Settings, get_settings
from app.core.errors import PipelineError
from app.core.logging import JsonFormatter, request_id
from app.core.ratelimit import RateLimiter
from app.core.security import hash_password, issue_token, read_token, verify_password
from app.db.models import Conversation, User, UserRole
from app.main import app
from app.services.chat import ChatService
from scripts.create_user import create_or_reset
from tests.test_api import api, submit  # noqa: F401  (fixture reused)

SECRET = 's' * 40


def make_user(session, name: str, role: UserRole = UserRole.user, password: str = 'password-123') -> User:
    user = User(username=name, password_hash=hash_password(password), role=role)
    session.add(user)
    session.commit()
    return user


def act_as(user: User) -> None:
    app.dependency_overrides[get_current_user] = lambda: user


@pytest.fixture
def real_auth(db_session):
    """Real cookie/bearer authentication (the default test override removed) with a known secret."""
    from app.db.session import get_db
    settings = Settings(_env_file=None, auth_secret=SECRET)
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_password_hash_and_token_round_trip():
    stored = hash_password('correct horse')
    assert stored.startswith('scrypt$') and verify_password('correct horse', stored)
    assert not verify_password('wrong', stored) and not verify_password('x', 'garbage')
    token = issue_token(SECRET, 'user-1', 3, 60)
    assert read_token(SECRET, token)['sub'] == 'user-1' and read_token(SECRET, token)['ver'] == 3
    payload, signature = token.split('.')
    assert read_token('another-secret' * 3, token) is None
    assert read_token(SECRET, f'{payload}x.{signature}') is None
    assert read_token(SECRET, issue_token(SECRET, 'user-1', 0, -1)) is None
    assert read_token(SECRET, 'not-a-token') is None


def test_signing_secret_is_required(tmp_path):
    with pytest.raises(RuntimeError):
        Settings(_env_file=None, auth_secret='', auth_secret_file=tmp_path / 'missing').signing_secret()
    (tmp_path / 'secret').write_text('f' * 48)
    assert Settings(_env_file=None, auth_secret='', auth_secret_file=tmp_path / 'secret').signing_secret() == 'f' * 48


def test_login_cookie_and_bearer_sessions(real_auth, db_session):
    make_user(db_session, 'alice')
    client = real_auth
    assert client.get('/api/documents').status_code == 401
    assert client.post('/api/auth/login', json={'username': 'alice', 'password': 'nope'}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'nobody', 'password': 'nope'}).json()['error'] \
        == 'invalid_credentials'
    response = client.post('/api/auth/login', json={'username': 'ALICE', 'password': 'password-123'})
    assert response.status_code == 200
    cookie = response.headers['set-cookie']
    assert f'{COOKIE_NAME}=' in cookie and 'HttpOnly' in cookie and 'SameSite=strict' in cookie
    assert client.get('/api/auth/me').json()['username'] == 'alice'
    token = response.json()['access_token']
    bearer = TestClient(app)
    assert bearer.get('/api/auth/me', headers={'Authorization': f'Bearer {token}'}).status_code == 200
    assert bearer.get('/api/auth/me', headers={'Authorization': 'Bearer forged.token'}).status_code == 401
    assert client.post('/api/auth/logout').status_code == 204
    assert client.get('/api/auth/me').status_code == 401


def test_password_change_and_deactivation_revoke_tokens(real_auth, db_session):
    alice = make_user(db_session, 'alice')
    admin = make_user(db_session, 'root', UserRole.admin)
    client = real_auth
    old = client.post('/api/auth/login', json={'username': 'alice', 'password': 'password-123'}).json()['access_token']
    changed = client.post('/api/auth/password', json={'current_password': 'password-123',
                                                      'new_password': 'new-password-456'})
    assert changed.status_code == 200
    assert TestClient(app).get('/api/auth/me', headers={'Authorization': f'Bearer {old}'}).status_code == 401
    fresh = changed.json()['access_token']
    assert TestClient(app).get('/api/auth/me', headers={'Authorization': f'Bearer {fresh}'}).status_code == 200
    act_as(admin)
    assert client.patch(f'/api/users/{alice.id}', json={'is_active': False}).status_code == 200
    app.dependency_overrides.pop(get_current_user)
    assert TestClient(app).get('/api/auth/me', headers={'Authorization': f'Bearer {fresh}'}).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'alice', 'password': 'new-password-456'}).status_code \
        == 401


def test_user_management_is_admin_only(real_auth, db_session):
    admin = make_user(db_session, 'root', UserRole.admin)
    bob = make_user(db_session, 'bob')
    client = real_auth
    act_as(bob)
    assert client.get('/api/users').status_code == 403
    assert client.post('/api/users', json={'username': 'eve', 'password': 'password-123'}).status_code == 403
    act_as(admin)
    created = client.post('/api/users', json={'username': 'carol', 'password': 'password-123'})
    assert created.status_code == 201 and created.json()['role'] == 'user' and 'password_hash' not in created.json()
    assert client.post('/api/users', json={'username': 'CAROL', 'password': 'password-123'}).status_code == 409
    assert client.post('/api/users', json={'username': 'x', 'password': 'short'}).status_code == 422
    assert [u['username'] for u in client.get('/api/users').json()] == ['bob', 'carol', 'root']
    assert client.patch(f'/api/users/{admin.id}', json={'is_active': False}).json()['error'] == 'cannot_demote_self'
    assert client.patch(f'/api/users/{uuid.uuid4()}', json={'role': 'admin'}).status_code == 404


def test_documents_are_private_until_shared(api, samples, db_session, db_user):
    client, *_ = api()
    alice, bob = make_user(db_session, 'alice'), make_user(db_session, 'bob')
    data = (samples / 'sample.pdf').read_bytes()
    act_as(alice)
    document = submit(client, 'alice.pdf', data).json()['document']
    assert document['is_shared'] is False and document['can_manage'] is True
    path = f"/api/documents/{document['id']}"
    act_as(bob)
    assert client.get('/api/documents').json()['total'] == 0
    for response in (client.get(path), client.get(f'{path}/file'), client.delete(path),
                     client.patch(path, json={'is_shared': True}), client.post(f'{path}/reindex')):
        assert response.status_code == 404
    # Same bytes from another user are a separate document, not a duplicate that reveals Alice's file.
    own = submit(client, 'bob.pdf', data)
    assert own.status_code == 202 and own.json()['duplicate'] is False
    act_as(alice)
    assert client.patch(path, json={'is_shared': True}).json()['is_shared'] is True
    act_as(bob)
    assert client.get(path).json()['can_manage'] is False
    assert client.get(f'{path}/file').status_code == 200
    assert client.delete(path).status_code == 403
    assert client.get('/api/documents').json()['total'] == 2
    act_as(db_user)  # admin: manages shared documents of others
    assert client.get(path).json()['can_manage'] is True
    assert client.delete(path).status_code == 204


def test_job_progress_is_only_visible_with_the_document(api, samples, db_session):
    client, *_ = api()
    alice, bob = make_user(db_session, 'alice'), make_user(db_session, 'bob')
    act_as(alice)
    job_id = submit(client, 'a.pdf', (samples / 'sample.pdf').read_bytes()).json()['job']['id']
    assert client.get(f'/api/jobs/{job_id}').status_code == 200
    act_as(bob)
    assert client.get(f'/api/jobs/{job_id}').status_code == 404
    assert client.get(f'/api/jobs/{job_id}/events').status_code == 404


def test_pdf_page_limit(api, db_session, db_user):
    client, *_ = api()
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, max_pdf_pages=2, max_upload_mb=1)
    with fitz.open() as pdf:
        for _ in range(3):
            pdf.new_page().insert_text((72, 72), 'page')
        data = pdf.tobytes()
    response = submit(client, 'long.pdf', data)
    assert response.status_code == 413 and response.json()['error'] == 'too_many_pages'


def test_chat_retrieval_and_conversations_are_per_user(db_engine, db_session):
    from sqlalchemy.orm import sessionmaker
    from app.db.models import Document, DocumentStatus
    from tests.test_chat import RecordingStore
    alice, bob = make_user(db_session, 'alice'), make_user(db_session, 'bob')

    def document(name, owner, shared):
        row = Document(filename=name, content_type='application/pdf', size_bytes=1, sha256=uuid.uuid4().hex * 2,
                       object_key=f'documents/{name}', status=DocumentStatus.ready, owner_id=owner.id,
                       is_shared=shared)
        db_session.add(row)
        db_session.commit()
        return str(row.id)
    private, shared = document('a.pdf', alice, False), document('s.pdf', alice, True)
    store = RecordingStore([])
    from app.services.retrieval import RetrievalService
    from tests.conftest import FakeEmbedding
    settings = Settings(_env_file=None)
    service = ChatService(sessionmaker(bind=db_engine, expire_on_commit=False),
                          RetrievalService(settings, FakeEmbedding(), store), None, None, settings)
    list(service.ask('Nghỉ phép bao nhiêu ngày?', user=bob))
    assert store.filters[-1] == [shared]
    list(service.ask('Nghỉ phép bao nhiêu ngày?', user=alice))
    assert sorted(store.filters[-1]) == sorted([private, shared])
    with pytest.raises(PipelineError) as error:
        service.ask('q', document_ids=[uuid.UUID(private)], user=bob)
    assert error.value.status_code == 404
    conversation = db_session.query(Conversation).filter_by(owner_id=alice.id).first()
    with pytest.raises(PipelineError):
        service.ask('q', conversation_id=conversation.id, user=bob)
    # A user who can read nothing never searches the whole store.
    carol = make_user(db_session, 'carol')
    db_session.query(Document).filter(Document.id == uuid.UUID(shared)).update({'is_shared': False})
    db_session.commit()
    before = len(store.filters)
    events = list(service.ask('Nghỉ phép bao nhiêu ngày?', user=carol))
    assert events[-1].kind == 'done' and events[-1].data['sources'] == []
    assert len(store.filters) == before


def test_conversations_api_hides_other_users(api, db_session):
    client, *_ = api()
    alice, bob = make_user(db_session, 'alice'), make_user(db_session, 'bob')
    conversation = Conversation(title='riêng', owner_id=alice.id)
    db_session.add(conversation)
    db_session.commit()
    act_as(bob)
    assert client.get('/api/conversations').json()['total'] == 0
    assert client.get(f'/api/conversations/{conversation.id}').status_code == 404
    assert client.delete(f'/api/conversations/{conversation.id}').status_code == 404
    act_as(alice)
    assert client.get('/api/conversations').json()['total'] == 1


def test_legacy_ask_is_scoped_to_readable_documents(api, db_session):
    client, *_ = api()
    app.dependency_overrides.pop(get_readable_document_ids)
    bob = make_user(db_session, 'bob')
    act_as(bob)
    # Nothing readable: the no-context answer, without calling the LLM.
    response = client.post('/api/questions/ask', json={'question': 'Nghỉ phép?'})
    assert response.status_code == 200 and response.json()['sources'] == []


class FakeRedis:
    def __init__(self, fail=False):
        self.counts, self.fail = {}, fail

    def pipeline(self):
        return self

    def incr(self, key):
        self.key = key

    def expire(self, key, seconds):
        pass

    def get(self, key):
        if self.fail:
            raise ConnectionError('down')
        return self.counts.get(key)

    def execute(self):
        if self.fail:
            raise ConnectionError('down')
        self.counts[self.key] = self.counts.get(self.key, 0) + 1
        return [self.counts[self.key], True]


def test_rate_limiter_limits_and_fails_open():
    limiter = RateLimiter(FakeRedis())
    limiter.check('chat', 'u1', 2)
    limiter.check('chat', 'u1', 2)
    with pytest.raises(PipelineError) as error:
        limiter.check('chat', 'u1', 2)
    assert error.value.status_code == 429
    limiter.check('chat', 'u2', 2)
    limiter.check('chat', 'u1', 0)  # 0 disables the limit
    assert not limiter.blocked('fail', 'k', 2)
    assert limiter.hit('fail', 'k') == 1 and limiter.hit('fail', 'k') == 2
    assert limiter.blocked('fail', 'k', 2) and not limiter.blocked('fail', 'k', 0)
    down = RateLimiter(FakeRedis(fail=True))
    down.check('chat', 'u1', 1)
    assert down.hit('fail', 'k') is None and not down.blocked('fail', 'k', 1)
    RateLimiter(None).check('chat', 'u1', 1)


@pytest.fixture
def login_limits(real_auth):
    from app.core.dependencies import get_rate_limiter
    limiter = RateLimiter(FakeRedis())
    app.dependency_overrides[get_rate_limiter] = lambda: limiter
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, auth_secret=SECRET,
                                                              rate_limit_login_per_minute=3)
    return real_auth


def login_status(client, password, ip, username='alice'):
    return client.post('/api/auth/login', json={'username': username, 'password': password},
                       headers={'X-Real-IP': ip}).status_code


def test_failed_logins_are_limited_per_username_and_ip(login_limits, db_session):
    make_user(db_session, 'alice')
    client = login_limits
    assert [login_status(client, 'bad', '10.0.0.9') for _ in range(4)] == [401, 401, 401, 429]
    # The attacker's failures do not lock the real user out from another machine...
    assert [login_status(client, 'password-123', '10.0.0.5') for _ in range(3)] == [200, 200, 200]
    # ...while every attempt from one IP is still capped.
    assert login_status(client, 'password-123', '10.0.0.5') == 429


def test_password_change_is_rate_limited(login_limits, db_session):
    act_as(make_user(db_session, 'alice'))
    codes = [login_limits.post('/api/auth/password', json={'current_password': 'guess', 'new_password': 'x' * 8})
             .status_code for _ in range(4)]
    assert codes == [400, 400, 400, 429]


def test_admin_cannot_reset_own_password_or_remove_last_admin(real_auth, db_session):
    root = make_user(db_session, 'root', UserRole.admin)
    other = make_user(db_session, 'other', UserRole.admin)
    act_as(root)
    response = real_auth.patch(f'/api/users/{root.id}', json={'password': 'new-password-1'})
    assert response.status_code == 409 and response.json()['error'] == 'use_password_change'
    # Another admin demoted `root` while root's request (authenticated before that) was in flight:
    # it must not remove the last active admin.
    act_as(SimpleNamespace(id=root.id, role=UserRole.admin, is_active=True))
    db_session.query(User).filter(User.id == root.id).update({'role': UserRole.user})
    db_session.commit()
    response = real_auth.patch(f'/api/users/{other.id}', json={'is_active': False})
    assert response.status_code == 409 and response.json()['error'] == 'last_admin'


def test_usernames_are_case_insensitive(real_auth, db_session):
    from sqlalchemy.exc import IntegrityError
    act_as(make_user(db_session, 'root', UserRole.admin))
    created = real_auth.post('/api/users', json={'username': 'Alice.N', 'password': 'password-123'})
    assert created.status_code == 201 and created.json()['username'] == 'alice.n'
    assert real_auth.post('/api/users', json={'username': 'ALICE.N', 'password': 'password-123'}).status_code == 409
    app.dependency_overrides.pop(get_current_user)
    login = real_auth.post('/api/auth/login', json={'username': 'Alice.N', 'password': 'password-123'})
    assert login.status_code == 200
    db_session.add(User(username='ALICE.n', password_hash='x', role=UserRole.user))
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_ownerless_documents_are_shared_and_owners_cannot_vanish(db_session):
    from sqlalchemy.exc import IntegrityError
    from app.db.models import Document, DocumentStatus
    from app.db.repositories import DocumentRepository
    bob = make_user(db_session, 'bob')

    def document(owner, shared):
        return Document(filename='x.pdf', content_type='application/pdf', size_bytes=1, sha256=uuid.uuid4().hex * 2,
                        object_key=f'documents/{uuid.uuid4()}.pdf', status=DocumentStatus.ready,
                        owner_id=owner.id if owner else None, is_shared=shared)
    legacy = document(None, True)
    db_session.add(legacy)
    db_session.commit()
    assert DocumentRepository(db_session).get_visible(legacy.id, bob) is not None
    defaulted = [Document(filename='d.pdf', content_type='application/pdf', size_bytes=1, sha256=uuid.uuid4().hex * 2,
                          object_key=f'documents/{uuid.uuid4()}.pdf', owner_id=owner) for owner in (None, bob.id)]
    db_session.add_all(defaulted)
    db_session.commit()
    assert [row.is_shared for row in defaulted] == [True, False]  # shared exactly when ownerless
    db_session.add(document(None, False))  # an ownerless private row would be visible to nobody
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
    db_session.add(document(bob, False))
    db_session.commit()
    db_session.delete(bob)  # deleting an owner must not turn private documents into shared ones
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_status_counts_only_readable_documents_for_users(monkeypatch, db_engine, db_session):
    from sqlalchemy.orm import sessionmaker
    from app.api import system
    from app.db.models import Document, DocumentStatus
    alice, bob = make_user(db_session, 'alice'), make_user(db_session, 'bob')
    for owner, shared in ((alice, False), (alice, False), (bob, True)):
        db_session.add(Document(filename='x.pdf', content_type='application/pdf', size_bytes=1,
                                sha256=uuid.uuid4().hex * 2, object_key=f'documents/{uuid.uuid4()}.pdf',
                                status=DocumentStatus.ready, owner_id=owner.id, is_shared=shared))
    db_session.commit()
    monkeypatch.setattr(system, 'dependency_checks', lambda: {'database': {'ok': True}, 'queue': {'ok': False}})
    monkeypatch.setattr(system, 'get_llm', lambda: type('LLM', (), {'health': lambda self: {}})())
    monkeypatch.setattr(system, 'get_sessionmaker', lambda: sessionmaker(bind=db_engine))
    with TestClient(app) as client:
        act_as(bob)
        assert client.get('/api/system/status').json()['documents_by_status'] == {'ready': 1}
        act_as(make_user(db_session, 'root', UserRole.admin))
        assert client.get('/api/system/status').json()['documents_by_status'] == {'ready': 3}


def test_job_stream_stops_when_access_is_revoked(monkeypatch, db_engine, db_session):
    from sqlalchemy.orm import sessionmaker
    from app.api import documents as documents_api
    from app.db.models import Document, DocumentStatus, IngestJob, JobStatus
    alice, bob = make_user(db_session, 'alice'), make_user(db_session, 'bob')
    document = Document(filename='x.pdf', content_type='application/pdf', size_bytes=1, sha256='c' * 64,
                        object_key='documents/x.pdf', status=DocumentStatus.processing, owner_id=alice.id,
                        is_shared=True)
    db_session.add(document)
    db_session.flush()
    job = IngestJob(document_id=document.id, status=JobStatus.running, progress=10, step='extract')
    db_session.add(job)
    db_session.commit()
    monkeypatch.setattr(documents_api, 'get_sessionmaker', lambda: sessionmaker(bind=db_engine))
    events = documents_api.job_events(job.id, bob.id, bob.token_version, 0.01, 5)
    assert next(events).startswith('event: progress')
    document.is_shared = False  # unshared while bob watches
    job.progress = 20
    db_session.commit()
    assert 'job_not_found' in next(events)
    events = documents_api.job_events(job.id, alice.id, alice.token_version, 0.01, 5)
    assert next(events).startswith('event: progress')
    alice.token_version += 1  # password changed elsewhere
    job.progress = 30
    db_session.commit()
    assert 'unauthorized' in next(events)


def test_health_ready_and_status(monkeypatch, db_session):
    from app.api import system
    monkeypatch.setattr(system, 'dependency_checks', lambda: {'database': {'ok': True, 'ms': 1.0},
                                                              'queue': {'ok': False, 'error': 'queue_unavailable'}})
    with TestClient(app) as client:
        assert client.get('/health').json() == {'status': 'ok'}
        ready = client.get('/health/ready')
        assert ready.status_code == 503 and ready.json()['checks']['queue']['error'] == 'queue_unavailable'
        app.dependency_overrides.pop(get_current_user)
        assert client.get('/api/system/status').status_code == 401


def test_status_reports_components(monkeypatch, db_session, signed_in):
    from app.api import system
    monkeypatch.setattr(system, 'dependency_checks', lambda: {'database': {'ok': False, 'error': 'x'},
                                                              'queue': {'ok': False, 'error': 'y'}})
    monkeypatch.setattr(system, 'get_llm', lambda: type('LLM', (), {'health': lambda self: {}})())
    with TestClient(app) as client:
        body = client.get('/api/system/status').json()
    assert body['status'] == 'degraded' and body['checks']['llm']['ok'] is True
    assert body['config']['max_pdf_pages'] > 0 and 'password' not in json.dumps(body).lower()


def test_json_logs_carry_request_id(capsys):
    record = logging.LogRecord('app.test', logging.INFO, __file__, 1, 'hello %s', ('world',), None)
    record.status = 200
    token = request_id.set('abc123')
    try:
        line = json.loads(JsonFormatter().format(record))
    finally:
        request_id.reset(token)
    assert line['message'] == 'hello world' and line['request_id'] == 'abc123' and line['status'] == 200
    with TestClient(app) as client:
        response = client.get('/health', headers={'X-Request-ID': 'req-1'})
        assert response.headers['x-request-id'] == 'req-1'
        assert len(client.get('/health', headers={'X-Request-ID': 'bad\x7fid'}).headers['x-request-id']) == 32


def test_create_user_script_and_first_user_adopts_conversations(db_session):
    db_session.add(Conversation(title='cũ'))
    db_session.commit()
    user, created = create_or_reset(db_session, 'admin', 'password-123', UserRole.admin)
    assert created and user.role == UserRole.admin
    assert db_session.query(Conversation).filter_by(owner_id=user.id).count() == 1
    again, created = create_or_reset(db_session, 'Admin', 'password-456')  # no role given: stays admin
    assert not created and again.role == UserRole.admin and again.token_version == 1
    assert verify_password('password-456', again.password_hash)
    demoted, _ = create_or_reset(db_session, 'admin', 'password-789', UserRole.user)
    assert demoted.role == UserRole.user
    with pytest.raises(ValueError):
        create_or_reset(db_session, 'bad name!', 'password-123', UserRole.user)
    with pytest.raises(ValueError):
        create_or_reset(db_session, 'short', 'x', UserRole.user)


def test_init_generates_signing_secret_once(tmp_path):
    from scripts.init_services import ensure_signing_secret
    settings = Settings(_env_file=None, auth_secret='', auth_secret_file=tmp_path / 'secrets' / 'auth_secret')
    ensure_signing_secret(settings)
    first = settings.signing_secret()
    assert len(first) >= 32 and (settings.auth_secret_file.stat().st_mode & 0o777) == 0o600
    ensure_signing_secret(settings)
    assert settings.signing_secret() == first
    explicit = Settings(_env_file=None, auth_secret=SECRET, auth_secret_file=tmp_path / 'unused')
    ensure_signing_secret(explicit)
    assert not (tmp_path / 'unused').exists()


def test_init_creates_first_admin_only_once(db_engine, db_session, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from scripts import init_services
    monkeypatch.setattr(init_services, 'get_sessionmaker', lambda: sessionmaker(bind=db_engine))
    monkeypatch.delenv('INITIAL_ADMIN_PASSWORD', raising=False)
    init_services.ensure_first_admin()
    assert db_session.query(User).count() == 0
    monkeypatch.setenv('INITIAL_ADMIN_USERNAME', 'boss')
    monkeypatch.setenv('INITIAL_ADMIN_PASSWORD', 'password-123')
    init_services.ensure_first_admin()
    monkeypatch.setenv('INITIAL_ADMIN_PASSWORD', 'other-password')
    init_services.ensure_first_admin()
    users = db_session.query(User).all()
    assert [(u.username, u.role) for u in users] == [('boss', UserRole.admin)]
    assert verify_password('password-123', users[0].password_hash)


def test_init_survives_invalid_first_admin(db_engine, db_session, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from scripts import init_services
    monkeypatch.setattr(init_services, 'get_sessionmaker', lambda: sessionmaker(bind=db_engine))
    monkeypatch.setenv('INITIAL_ADMIN_USERNAME', 'admin@corp')
    monkeypatch.setenv('INITIAL_ADMIN_PASSWORD', 'short')
    init_services.ensure_first_admin()
    assert db_session.query(User).count() == 0
