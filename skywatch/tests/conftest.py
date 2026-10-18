from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.openmeteo import OpenMeteo

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"


def make_forecast(
    *,
    past_days: int = 7,
    forecast_days: int = 3,
    base_temp: float = 25.0,
    current: dict | None = None,
    hourly_overrides: dict[str, dict[int, float]] | None = None,
    now: datetime | None = None,
) -> dict:
    """Synthetic Open-Meteo payload. Hour index of 'now' is past_days*24 + current hour-of-day."""
    now = now or datetime.now(UTC)
    start = (now - timedelta(days=past_days)).replace(hour=0, minute=0, second=0, microsecond=0)
    n = (past_days + forecast_days) * 24
    times = [(start + timedelta(hours=k)).strftime("%Y-%m-%dT%H:%M") for k in range(n)]
    hourly = {
        "time": times,
        "temperature_2m": [base_temp + ((k % 24) - 12) * 0.3 for k in range(n)],  # small diurnal wave
        "apparent_temperature": [base_temp for _ in range(n)],
        "precipitation": [0.0] * n,
        "precipitation_probability": [0] * n,
        "weather_code": [1] * n,
        "wind_gusts_10m": [15.0] * n,
        "pressure_msl": [1012.0] * n,
    }
    for field, points in (hourly_overrides or {}).items():
        for idx, val in points.items():
            hourly[field][idx] = val
    cur = {
        "time": now.strftime("%Y-%m-%dT%H:%M"),
        "interval": 900,
        "temperature_2m": base_temp,
        "relative_humidity_2m": 55,
        "apparent_temperature": base_temp,
        "precipitation": 0.0,
        "weather_code": 1,
        "wind_speed_10m": 8.0,
        "wind_gusts_10m": 15.0,
        "pressure_msl": 1012.0,
        "uv_index": 4.0,
        "is_day": 1,
    }
    cur.update(current or {})
    return {
        "latitude": 28.6,
        "longitude": 77.2,
        "timezone": "UTC",
        "utc_offset_seconds": 0,
        "current": cur,
        "hourly": hourly,
    }


def now_index(past_days: int = 7, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    return past_days * 24 + now.hour


@pytest.fixture
def settings(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<html><body>ok</body></html>")
    return Settings(
        rate_limit_per_minute=600,
        rate_limit_burst=5,
        static_dir=str(static),
        upstream_retries=1,
        log_level="WARNING",
        _env_file=None,
    )


@pytest.fixture
def mock_api():
    with respx.mock(assert_all_called=False) as router:
        router.get(FORECAST_URL, name="forecast").mock(return_value=httpx.Response(200, json=make_forecast()))
        router.get(GEOCODE_URL).mock(
            return_value=httpx.Response(
                200,
                json={
                    "results": [
                        {"name": "Rajkot", "admin1": "Gujarat", "country": "India", "latitude": 22.3, "longitude": 70.8}
                    ]
                },
            )
        )
        yield router


@pytest.fixture
def client(settings, mock_api):
    app = create_app(settings, upstream=OpenMeteo(settings))
    with TestClient(app) as c:
        yield c
