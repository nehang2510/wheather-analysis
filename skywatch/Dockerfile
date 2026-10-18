FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /srv
COPY pyproject.toml ./
COPY app ./app
COPY static ./static
RUN pip install --no-cache-dir . && adduser --disabled-password --gecos "" app && chown -R app /srv
USER app
EXPOSE 8080
# Cloud Run injects $PORT; one worker per instance, let Cloud Run scale instances.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --proxy-headers --forwarded-allow-ips='*'"]
