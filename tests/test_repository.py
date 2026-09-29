import uuid

import pytest
from sqlalchemy.exc import IntegrityError

from app.db.models import Chunk, Conversation, Document, DocumentStatus, IngestJob, Message, MessageRole
from app.db.repositories import DocumentRepository


def document(name='a.pdf', sha=None):
    document_id = uuid.uuid4()
    return Document(id=document_id, filename=name, content_type='application/pdf', size_bytes=10,
                    sha256=sha or uuid.uuid4().hex * 2, object_key=f'documents/{document_id}.pdf')


def test_sha256_is_unique(db_session):
    repository = DocumentRepository(db_session)
    repository.add(document(sha='a' * 64))
    db_session.commit()
    with pytest.raises(IntegrityError):
        repository.add(document(sha='a' * 64))
    db_session.rollback()
    assert repository.get_by_sha256('a' * 64).status == DocumentStatus.pending


def test_chunks_are_ordered_and_cascade_with_document(db_session):
    repository = DocumentRepository(db_session)
    stored = repository.add(document())
    repository.replace_chunks(stored.id, [
        Chunk(document_id=stored.id, ordinal=i, page=1, content=f'c{i}', char_start=i, char_end=i + 1,
              bboxes=[{'page': 1, 'x0': 1.5, 'y0': 2, 'x1': 3, 'y1': 4}]) for i in (2, 0, 1)])
    db_session.add(IngestJob(document_id=stored.id, progress=50, step='ocr', warnings=[{'page': 2}]))
    db_session.commit()
    chunks = repository.chunks(stored.id)
    assert [chunk.ordinal for chunk in chunks] == [0, 1, 2]
    assert chunks[0].bboxes == [{'page': 1, 'x0': 1.5, 'y0': 2, 'x1': 3, 'y1': 4}]
    assert set(repository.chunks_by_ids([chunks[0].id, uuid.uuid4()])) == {chunks[0].id}
    repository.replace_chunks(stored.id, [Chunk(document_id=stored.id, ordinal=0, content='new', char_start=0,
                                                char_end=3)])
    assert [chunk.content for chunk in repository.chunks(stored.id)] == ['new']
    repository.delete(stored)
    db_session.commit()
    assert db_session.query(Chunk).count() == 0 and db_session.query(IngestJob).count() == 0


def test_list_orders_filters_and_escapes(db_session):
    repository = DocumentRepository(db_session)
    for name in ('Hợp đồng.pdf', 'hop_dong_2.pdf', 'Quy chế 100%.pdf'):
        repository.add(document(name))
        db_session.commit()
    items, total = repository.list(1, 10)
    assert total == 3 and items[0].filename == 'Quy chế 100%.pdf'
    assert [d.filename for d in repository.list(1, 10, 'HỢP ĐỒNG')[0]] == ['Hợp đồng.pdf']
    assert [d.filename for d in repository.list(1, 10, '100%')[0]] == ['Quy chế 100%.pdf']
    assert repository.list(1, 10, 'hop_')[1] == 1  # '_' is literal, not a wildcard
    assert repository.list(2, 2)[0][0].filename == 'Hợp đồng.pdf'


def test_conversation_messages_keep_sources(db_session):
    conversation = Conversation(title='Nghỉ phép')
    conversation.messages = [Message(role=MessageRole.user, content='Bao nhiêu ngày?'),
                             Message(role=MessageRole.assistant, content='12 ngày [1]',
                                     sources=[{'n': 1, 'chunk_id': str(uuid.uuid4()), 'page': 2}])]
    db_session.add(conversation)
    db_session.commit()
    stored = db_session.get(Conversation, conversation.id)
    assert [m.role for m in stored.messages] == [MessageRole.user, MessageRole.assistant]
    assert stored.messages[1].sources[0]['page'] == 2
    db_session.delete(stored)
    db_session.commit()
    assert db_session.query(Message).count() == 0
