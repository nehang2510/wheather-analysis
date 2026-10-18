import asyncio
import logging
import random

import httpx

from app.config import Settings
from app.schemas import Place

log = logging.getLogger("skywatch.upstream")

CURRENT_FIELDS = (
    "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,"
    "wind_speed_10m,wind_gusts_10m,pressure_msl,uv_index,is_day"
)
HOURLY_FIELDS = (
    "temperature_2m,apparent_temperature,precipitation,precipitation_probability,weather_code,"
    "wind_gusts_10m,pressure_msl"
)


class UpstreamError(Exception):
    """Raised after retries are exhausted or on a non-retryable upstream response."""


class OpenMeteo:
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None) -> None:
        self.s = settings
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(settings.upstream_timeout_s),
            headers={"User-Agent": f"{settings.app_name}/0.2"},
            limits=httpx.Limits(max_connections=20),
        )

    async def aclose(self) -> None:
        await self.client.aclose()

    async def _get(self, url: str, params: dict) -> dict | list:
        last_exc: Exception | None = None
        for attempt in range(self.s.upstream_retries + 1):
            try:
                res = await self.client.get(url, params=params)
                if res.status_code == 429 or res.status_code >= 500:
                    raise httpx.HTTPStatusError(f"HTTP {res.status_code}", request=res.request, response=res)
                if res.status_code >= 400:
                    raise UpstreamError(f"Open-Meteo rejected the request: {res.text[:200]}")
                try:
                    return res.json()
                except ValueError as exc:  # truncated or non-JSON body: treat like a transport failure and retry
                    raise httpx.TransportError(f"malformed upstream body: {exc}") from exc
            except (httpx.TransportError, httpx.HTTPStatusError) as exc:
                last_exc = exc
                delay = 0.6 * (2**attempt) + random.uniform(0, 0.3)  # noqa: S311 (jitter, not crypto)
                log.warning(
                    "upstream retry",
                    extra={"url": url, "attempt": attempt + 1, "error": str(exc), "delay_s": round(delay, 2)},
                )
                if attempt < self.s.upstream_retries:
                    await asyncio.sleep(delay)
        raise UpstreamError(f"Open-Meteo unavailable after {self.s.upstream_retries + 1} attempts: {last_exc}")

    async def forecast(self, lat: float, lon: float, forecast_days: int | None = None) -> dict:
        params = {
            "latitude": f"{lat:.4f}",
            "longitude": f"{lon:.4f}",
            "timezone": "auto",
            "past_days": self.s.past_days,
            "forecast_days": forecast_days or self.s.forecast_days,
            "current": CURRENT_FIELDS,
            "hourly": HOURLY_FIELDS,
        }
        data = await self._get(self.s.openmeteo_forecast_url, params)
        if not isinstance(data, dict):
            raise UpstreamError("Unexpected upstream payload shape")
        return data

    async def forecast_many(self, places: list[Place], forecast_days: int = 1) -> list[dict]:
        """One request for N locations. Open-Meteo bills it as N requests against the free quota."""
        params = {
            "latitude": ",".join(f"{p.latitude:.2f}" for p in places),
            "longitude": ",".join(f"{p.longitude:.2f}" for p in places),
            "timezone": "auto",
            "past_days": self.s.past_days,
            "forecast_days": forecast_days,
            "current": CURRENT_FIELDS,
            "hourly": HOURLY_FIELDS,
        }
        data = await self._get(self.s.openmeteo_forecast_url, params)
        if isinstance(data, dict):  # a single location comes back as an object
            data = [data]
        if len(data) != len(places):
            raise UpstreamError(f"Expected {len(places)} locations, got {len(data)}")
        return data

    async def geocode(self, query: str, count: int = 6) -> list[dict]:
        data = await self._get(
            self.s.openmeteo_geocode_url, {"name": query, "count": count, "language": "en", "format": "json"}
        )
        return data.get("results", []) if isinstance(data, dict) else []
