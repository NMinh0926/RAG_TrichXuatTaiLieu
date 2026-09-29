"""Worker recovery, job progress bookkeeping and positioned-text helpers."""
import uuid

from sqlalchemy.orm import sessionmaker

from app.db.models import Document, IngestJob, JobStatus
from app.services.jobs import Progress
from app.services.layout import Placed, Word, build_text, chunk_boxes
from app.worker import recover


class RecordingQueue:
    def __init__(self):
        self.enqueued = []

    def enqueue(self, job_id):
        self.enqueued.append(job_id)


def _job(session, status, step='extract', progress=40) -> IngestJob:
    document = Document(filename='a.pdf', content_type='application/pdf', size_bytes=1, sha256=uuid.uuid4().hex * 2,
                        object_key='documents/a.pdf')
    session.add(document)
    session.flush()
    job = IngestJob(document_id=document.id, status=status, step=step, progress=progress)
    session.add(job)
    session.commit()
    return job


def test_recover_requeues_crashed_and_lost_jobs_only(db_session, db_engine):
    running = _job(db_session, JobStatus.running)
    lost = _job(db_session, JobStatus.queued)
    waiting = _job(db_session, JobStatus.queued)
    done = _job(db_session, JobStatus.succeeded, 'done', 100)
    queue = RecordingQueue()
    count = recover(sessionmaker(bind=db_engine), queue, waiting=lambda job_id: job_id == str(waiting.id))
    assert count == 2 and set(queue.enqueued) == {running.id, lost.id}
    db_session.expire_all()
    restarted = db_session.get(IngestJob, running.id)
    assert (restarted.status, restarted.step, restarted.progress) == (JobStatus.queued, 'queued', 0)
    assert db_session.get(IngestJob, done.id).status == JobStatus.succeeded


def test_progress_is_monotonic_and_throttled(db_session, db_engine):
    job = _job(db_session, JobStatus.running, 'download', 2)
    progress = Progress(sessionmaker(bind=db_engine), job.id, interval=3600)
    progress('extract', 0.5)
    db_session.expire_all()
    assert (db_session.get(IngestJob, job.id).step, db_session.get(IngestJob, job.id).progress) == ('extract', 32)
    progress('extract', 0.9)  # same step inside the throttle interval: not written
    db_session.expire_all()
    assert db_session.get(IngestJob, job.id).progress == 32
    progress('embed', 1.0)
    db_session.expire_all()
    assert db_session.get(IngestJob, job.id).progress == 96
    progress.warn({'page': 3, 'error': 'ocr_failure'})
    assert progress.warnings == [{'page': 3, 'error': 'ocr_failure'}]
    timings = progress.finish()
    assert set(timings) == {'extract', 'embed'} and all(value >= 0 for value in timings.values())


def test_build_text_spans_and_line_boxes():
    words = [Word('Điều', 10, 10, 40, 20, block=1, line=1), Word('1.', 42, 10, 50, 20, block=1, line=1),
             Word('Nghỉ', 10, 30, 40, 40, block=1, line=2), Word('phép', 42, 30, 70, 40, block=1, line=2),
             Word('Mục', 10, 60, 30, 70, block=2, line=1)]
    text, placed = build_text(words)
    assert text == 'Điều 1.\nNghỉ phép\n\nMục'
    assert placed[2] == Placed(8, 12, 10, 30, 40, 40)
    assert text[placed[3].start:placed[3].end] == 'phép'
    # A range covering the end of line 1 and all of line 2 → two rectangles.
    assert chunk_boxes(placed, 5, 17, page=3) == [
        {'page': 3, 'x0': 42, 'y0': 10, 'x1': 50, 'y1': 20}, {'page': 3, 'x0': 10, 'y0': 30, 'x1': 70, 'y1': 40}]
    assert chunk_boxes(placed, 100, 120, page=1) == []
