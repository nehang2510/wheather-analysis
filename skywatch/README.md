# Skywatch

Weather anomaly monitor. A FastAPI backend pulls Open-Meteo forecasts, runs an eight-rule anomaly engine
(severe weather codes, gusts, heat/cold stress, heavy rain, pressure tendency, temperature swings, a z-score
against the trailing week, UV), scores each location 0 to 100, and serves a single-page frontend that alerts
with toasts, sound and desktop notifications. An India board watches all 36 state and UT capitals in one
batched upstream call.

## Architecture

```
browser (static/index.html)
   |  GET /api/anomalies?lat&lon&units      one city, full hourly history + analysis
   |  GET /api/india/board?units            36 capitals, ranked
   |  GET /api/india/places                 state -> cities dataset
   |  GET /api/geocode?q=                   city search
   v
FastAPI (app/main.py)
   - token-bucket rate limit per client IP        app/ratelimit.py
   - TTL cache with single-flight                  app/cache.py
   - request logging (JSON lines, X-Request-ID)    app/logging_setup.py
   - anomaly engine, pure functions, unit tested   app/analysis.py
   v
Open-Meteo (app/openmeteo.py: timeout, retries with jittered backoff, 502 on exhaustion)
```

The browser never talks to Open-Meteo directly, so one cache shared by every visitor is what hits the
upstream quota, and the thresholds live in one place (`app/analysis.py`, `Thresholds`).

## Run locally

```bash
uv venv && uv pip install -e ".[dev]"
uv run uvicorn app.main:app --reload --port 8000
# open http://localhost:8000      API docs at http://localhost:8000/api/docs
```

Tests and lint:

```bash
uv run pytest -q
uv run ruff check . && uv run ruff format --check .
```

Configuration is via `SKYWATCH_*` environment variables (see `.env.example` and `app/config.py`).

## Deploy to Google Cloud Run (free tier)

Cloud Run needs a billing account attached to the project, but the free tier (2M requests/month) covers
this app. The container listens on `$PORT`, logs JSON to stdout (picked up by Cloud Logging), and trusts
`X-Forwarded-For` so rate limiting sees real client IPs.

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com artifactregistry.googleapis.com cloudbuild.googleapis.com

gcloud run deploy skywatch \
  --source . \
  --region asia-south1 \
  --allow-unauthenticated \
  --min-instances 0 --max-instances 2 --cpu 1 --memory 256Mi \
  --set-env-vars SKYWATCH_LOG_LEVEL=INFO,SKYWATCH_RATE_LIMIT_PER_MINUTE=60
```

The command prints the service URL. The frontend is served from the same container at `/`, so that URL is
the whole app. If you prefer Firebase Hosting for the static page (custom domain, CDN), keep the API on
Cloud Run and add a rewrite in `firebase.json`:

```json
{ "hosting": { "public": "static", "rewrites": [
  { "source": "/api/**", "run": { "serviceId": "skywatch", "region": "asia-south1" } } ] } }
```

Then `firebase deploy`. Same-origin is preserved, so `SKYWATCH_CORS_ORIGINS` can stay empty.

## Operational notes

- Cache is in-process. With `--max-instances 1` every visitor shares it. If you scale out, each instance
  keeps its own cache and the upstream quota is split; swap `TTLCache` for Redis (Memorystore has no free
  tier, Upstash does) and keep the same `get_or_set` interface.
- Open-Meteo free quota is 10,000 requests/day; the board costs 36 per refresh. At the default 10-minute
  board TTL that is a ceiling of 5,184/day for the board no matter how many viewers, plus one per distinct
  city cell per 5 minutes.
- Rate limiting is per IP, also in-process. Behind Cloud Run that is correct because the platform sets
  `X-Forwarded-For`; if you ever front it with something else, set `SKYWATCH_TRUST_PROXY_HEADERS=false`
  or the limiter can be spoofed.
- The z-score baseline is the trailing 7 days, so a week-long heat wave becomes "normal". The archive
  endpoint (`archive-api.open-meteo.com`) gives a 30-day climatology if you want a stabler baseline.
- This is model forecast data, not an official warning service. Do not present it as one.
