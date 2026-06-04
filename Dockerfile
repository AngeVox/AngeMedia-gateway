FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY scripts ./scripts
COPY docs ./docs
COPY app ./app

ENV PROXY_HOST=0.0.0.0 \
    PROXY_PORT=9890 \
    IMAGE_PROXY_STATE_DIR=/data \
    PUBLIC_BASE_URL=http://localhost:9890

RUN mkdir -p /data/generated /data/uploads

EXPOSE 9890

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:9890/health', timeout=4)" || exit 1

CMD ["python3", "scripts/proxy.py"]
