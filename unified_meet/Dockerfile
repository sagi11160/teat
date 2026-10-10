FROM mcr.microsoft.com/playwright/python:v1.63.0-noble

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    MB_HOST=0.0.0.0 \
    MB_PORT=8080 \
    MB_DATA_DIR=/data \
    MB_DATABASE_URL=sqlite:////data/meetbridge.db \
    MB_LOG_JSON=true

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY alembic.ini ./
COPY migrations ./migrations
COPY meetbridge ./meetbridge

RUN useradd --create-home --uid 10001 meetbridge && mkdir -p /data && chown meetbridge /data
USER meetbridge
VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/readyz', timeout=4).status == 200 else 1)"
CMD ["python", "-m", "meetbridge.main"]
