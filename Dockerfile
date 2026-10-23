FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/app/data/library.db

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app.py dsa.py ./
COPY templates ./templates
COPY static ./static

RUN useradd --create-home --uid 10001 libms && mkdir -p /app/data && chown libms:libms /app/data
USER libms

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/login', timeout=2)" || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:8000", "--workers", "1", "--threads", "4", "app:app"]
