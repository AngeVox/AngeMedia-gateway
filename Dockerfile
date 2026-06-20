FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    IMAGE_PROXY_STATE_DIR=/app/state \
    OUTPUT_DIR=/app/generated \
    UPLOAD_DIR=/app/uploads \
    ANGEMEDIA_DB_FILE=/app/state/angemedia.db \
    QUOTA_FILE=/app/state/quota_state.json \
    PUBLIC_BASE_URL=http://localhost:9892 \
    PYTHONPATH=/app/scripts

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

COPY --chown=app:app scripts ./scripts
COPY --chown=app:app app ./app
COPY --chown=app:app README.md README_CN.md LICENSE ./

RUN mkdir -p /app/state /app/generated /app/uploads \
    && chown -R app:app /app/state /app/generated /app/uploads

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

CMD ["python", "-m", "uvicorn", "angemedia_gateway.server:app", "--host", "0.0.0.0", "--port", "8000"]
