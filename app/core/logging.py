import logging
import traceback


def configure_logging():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    # Per-request HTTP logs are noise; Ollama errors are logged by the client.
    for name in ('httpx', 'httpcore'):
        logging.getLogger(name).setLevel(logging.CRITICAL)


def log_failure(logger, code, exc):
    """Log stack locations, never exception payloads/locals with keys or documents."""
    frames = []
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        frames.extend(f'  File {frame.filename!r}, line {frame.lineno}, in {frame.name}\n'
                      for frame in traceback.extract_tb(exc.__traceback__))
        exc = exc.__cause__
    logger.error('failure=%s\n%s', code, ''.join(frames))
