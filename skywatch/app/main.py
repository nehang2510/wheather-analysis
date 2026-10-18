import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.analysis import analyze
from app.cache import TTLCache
from app.config import Settings, get_settings
from app.india import as_places, capitals
from app.logging_setup import configure_logging
from app.openmeteo import OpenMeteo, UpstreamError
from app.ratelimit import TokenBucketLimiter
from app.schemas import AnomalyResponse, BoardRow, Health, Place, Units

log = logging.getLogger("skywatch.api")


def create_app(settings: Settings | None = None, upstream: OpenMeteo | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    cache = TTLCache(settings.cache_max_entries)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.upstream = upstream or OpenMeteo(settings)
        app.state.cache = cache
        log.info("startup", extra={"version": __version__, "debug": settings.debug})
        yield
        await app.state.upstream.aclose()

    app = FastAPI(
        title="Skywatch API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
    )

    # Middleware order: outermost first. Rate limit before anything expensive.
    app.add_middleware(
        TokenBucketLimiter,
        per_minute=settings.rate_limit_per_minute,
        burst=settings.rate_limit_burst,
        trust_proxy=settings.trust_proxy_headers,
    )
    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["GET"], allow_headers=["*"]
        )

    @app.middleware("http")
    async def request_log(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled", extra={"request_id": rid, "path": request.url.path})
            return JSONResponse(
                {"detail": "Internal error", "request_id": rid}, status_code=500, headers={"X-Request-ID": rid}
            )
        response.headers["X-Request-ID"] = rid
        if request.url.path.startswith("/api/"):
            log.info(
                "request",
                extra={
                    "request_id": rid,
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
                },
            )
        return response

    @app.exception_handler(UpstreamError)
    async def upstream_error(_: Request, exc: UpstreamError):
        return JSONResponse({"detail": str(exc)}, status_code=502)

    def deps(request: Request) -> tuple[OpenMeteo, TTLCache]:
        return request.app.state.upstream, request.app.state.cache

    Deps = Annotated[tuple[OpenMeteo, TTLCache], Depends(deps)]
    Lat = Annotated[float, Query(ge=-90, le=90)]
    Lon = Annotated[float, Query(ge=-180, le=180)]

    @app.get("/api/health", response_model=Health)
    async def health(d: Deps):
        _, c = d
        return Health(status="ok", version=__version__, cache_entries=len(c), cache_hits=c.hits, cache_misses=c.misses)

    @app.get("/api/geocode")
    async def geocode(d: Deps, q: Annotated[str, Query(min_length=2, max_length=80)]):
        om, c = d
        key = f"geo:{q.strip().lower()}"
        hits = await c.get_or_set(key, settings.geocode_ttl_s, lambda: om.geocode(q.strip()))
        return [
            Place(
                name=h["name"],
                admin1=h.get("admin1"),
                country=h.get("country"),
                latitude=h["latitude"],
                longitude=h["longitude"],
            )
            for h in hits
        ]

    @app.get("/api/anomalies", response_model=AnomalyResponse)
    async def anomalies(d: Deps, lat: Lat, lon: Lon, units: Units = "metric", name: str | None = None):
        om, c = d
        # Round the cache key so nearby coordinates share one upstream call (a forecast cell is ~10 km).
        key = f"fc:{round(lat, 2)}:{round(lon, 2)}"
        data = await c.get_or_set(key, settings.forecast_ttl_s, lambda: om.forecast(lat, lon))
        place = Place(name=name, latitude=lat, longitude=lon) if name else None
        return AnomalyResponse(
            place=place, units=units, data=data, analysis=analyze(data, units, past_days=settings.past_days)
        )

    @app.get("/api/india/places")
    async def india_places():
        return as_places()

    @app.get("/api/india/board", response_model=list[BoardRow])
    async def india_board(d: Deps, units: Units = "metric"):
        om, c = d
        places = capitals()
        rows = await c.get_or_set("board:india", settings.board_ttl_s, lambda: om.forecast_many(places))
        out = [
            BoardRow(place=p, current=row["current"], analysis=analyze(row, units, past_days=settings.past_days))
            for p, row in zip(places, rows, strict=True)
        ]
        out.sort(key=lambda r: (-r.analysis.score, r.place.admin1 or ""))
        return out

    @app.get("/api/{rest:path}")
    async def api_404(rest: str):
        raise HTTPException(404, f"No such endpoint: /api/{rest}")

    static = Path(settings.static_dir)
    if static.is_dir():
        app.mount("/", StaticFiles(directory=static, html=True), name="static")
    return app


app = create_app()
