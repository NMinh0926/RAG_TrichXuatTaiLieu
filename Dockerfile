FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    PADDLE_PDX_CACHE_HOME=/app/.cache/paddlex \
    PADDLE_HOME=/app/.cache/paddle

WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 libgl1 libglib2.0-0 fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

# Separate layer: changing requirements.lock.txt must not re-download torch.
# CPU wheels avoid installing the large CUDA runtime on this CPU backend.
RUN pip install --index-url https://download.pytorch.org/whl/cpu torch==2.14.0
COPY requirements.lock.txt ./
RUN pip install -r requirements.lock.txt

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY scripts ./scripts
COPY tests ./tests
COPY sample_data ./sample_data
COPY evaluation ./evaluation
COPY pytest.ini ./

RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data/chroma /app/.cache \
    && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
