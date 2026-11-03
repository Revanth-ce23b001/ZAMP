# ClearMatch AP backend. Used by Render (render.yaml) and runnable locally:
#   docker build -t clearmatch-backend backend
#   docker run -p 8000:8000 --env-file backend/.env clearmatch-backend
#
# The disk is ephemeral on Render's free plan. That is fine by design: on startup the app applies
# migrations, loads the fixtures-v1 seed into an empty database (NFR-12) and loads the shipped
# extraction-cache snapshot into an empty cache (DEC-014), so a fresh container serves the demo
# fully cached. Runtime data (database, uploads) lives under /app/data.
FROM python:3.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DATABASE_PATH=/app/data/clearmatch.db \
    UPLOADS_DIR=/app/data/uploads

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY seed ./seed
COPY samples ./samples

RUN useradd --create-home appuser && mkdir -p /app/data/uploads && chown -R appuser /app/data
USER appuser

EXPOSE 8000
# Render injects PORT. One worker process: the sequential worker and in-memory rate limits assume a
# single instance (A-22). No --proxy-headers: trusting arbitrary X-Forwarded-For would let direct
# callers pick their own reset-limit bucket; UI requests carry the trusted X-Client-IP (DEC-011).
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
