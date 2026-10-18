"""Anomaly engine. Pure functions over an Open-Meteo forecast payload; no I/O, fully unit-testable."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from statistics import mean, stdev

from app.schemas import Alert, Analysis, Stats

WMO: dict[int, tuple[str, str]] = {
    0: ("Clear sky", "calm"),
    1: ("Mostly clear", "calm"),
    2: ("Partly cloudy", "calm"),
    3: ("Overcast", "calm"),
    45: ("Fog", "watch"),
    48: ("Freezing fog", "warning"),
    51: ("Light drizzle", "calm"),
    53: ("Drizzle", "calm"),
    55: ("Heavy drizzle", "watch"),
    56: ("Freezing drizzle", "warning"),
    57: ("Heavy freezing drizzle", "critical"),
    61: ("Light rain", "calm"),
    63: ("Rain", "watch"),
    65: ("Heavy rain", "warning"),
    66: ("Freezing rain", "critical"),
    67: ("Heavy freezing rain", "critical"),
    71: ("Light snow", "watch"),
    73: ("Snow", "warning"),
    75: ("Heavy snow", "critical"),
    77: ("Snow grains", "watch"),
    80: ("Rain showers", "calm"),
    81: ("Heavy showers", "watch"),
    82: ("Violent showers", "critical"),
    85: ("Snow showers", "warning"),
    86: ("Heavy snow showers", "critical"),
    95: ("Thunderstorm", "critical"),
    96: ("Thunderstorm with hail", "critical"),
    99: ("Severe thunderstorm with hail", "critical"),
}
LEVEL_RANK = {"calm": 0, "watch": 1, "warning": 2, "critical": 3}
WEIGHTS = {"watch": 15, "warning": 35, "critical": 70}


@dataclass(frozen=True, slots=True)
class Thresholds:
    gust_warn: float = 60.0  # km/h
    gust_crit: float = 90.0
    heat_warn: float = 35.0  # apparent °C
    heat_crit: float = 40.0
    cold_warn: float = -5.0
    cold_crit: float = -15.0
    rain_warn: float = 8.0  # mm in one hour
    rain_crit: float = 20.0
    pres_drop_warn: float = 3.0  # hPa over 3 h
    pres_drop_crit: float = 6.0
    temp_jump_warn: float = 8.0  # °C over 3 h
    z_watch: float = 2.0
    z_warn: float = 2.5
    z_crit: float = 3.0
    uv_warn: float = 8.0
    uv_crit: float = 11.0


DEFAULT_THRESHOLDS = Thresholds()


# --- formatting -------------------------------------------------------------------------------
def fmt_temp(c: float, imperial: bool) -> str:
    return f"{round(c * 9 / 5 + 32)}°F" if imperial else f"{round(c)}°C"


def fmt_delta(c: float, imperial: bool) -> str:
    return f"{round(c * 9 / 5)}°F" if imperial else f"{round(c)}°C"


def fmt_speed(kmh: float, imperial: bool) -> str:
    return f"{round(kmh * 0.621371)} mph" if imperial else f"{round(kmh)} km/h"


def fmt_time(local_iso: str) -> str:
    dt = datetime.fromisoformat(local_iso)
    hour = dt.hour % 12 or 12
    return f"{hour}:{dt.minute:02d} {'PM' if dt.hour >= 12 else 'AM'}"


# --- helpers ------------------------------------------------------------------------------------
def _finite(values: list) -> list[float]:
    return [float(v) for v in values if isinstance(v, int | float) and v == v]


def current_hour_index(times: list[str], offset_s: int, now: datetime | None = None) -> int:
    """Open-Meteo hourly timestamps are location-local with no offset; anchor them with utc_offset_seconds."""
    now = now or datetime.now(UTC)
    tz = timezone(timedelta(seconds=offset_s))
    best, best_diff = 0, float("inf")
    for i, t in enumerate(times):
        diff = abs((datetime.fromisoformat(t).replace(tzinfo=tz) - now).total_seconds())
        if diff < best_diff:
            best, best_diff = i, diff
    return best


def _at(values: list, i: int) -> float | None:
    if 0 <= i < len(values) and isinstance(values[i], int | float):
        return float(values[i])
    return None


# --- engine -------------------------------------------------------------------------------------
def analyze(
    data: dict,
    units: str = "metric",
    thresholds: Thresholds = DEFAULT_THRESHOLDS,
    past_days: int = 7,
    now: datetime | None = None,
) -> Analysis:
    th = thresholds
    imperial = units == "imperial"
    c = data["current"]
    h = data["hourly"]
    i = current_hour_index(h["time"], int(data.get("utc_offset_seconds", 0)), now)
    alerts: list[Alert] = []

    def push(id_: str, level: str, title: str, detail: str) -> None:
        alerts.append(Alert(id=id_, level=level, title=title, detail=detail))  # type: ignore[arg-type]

    # 1. Severe weather code, now and in the next 12 hours
    code = int(c.get("weather_code", -1))
    desc, lvl = WMO.get(code, ("Unknown", "calm"))
    if lvl != "calm":
        push(f"code-{code}", lvl, desc, "Reported for the current hour.")
    for k in range(i + 1, min(i + 12, len(h["time"]) - 1) + 1):
        d2, l2 = WMO.get(int(h["weather_code"][k] or -1), ("", "calm"))
        if l2 == "critical":
            push(
                f"incoming-{h['weather_code'][k]}",
                "warning",
                f"{d2} expected",
                f"Forecast at {fmt_time(h['time'][k])}.",
            )
            break

    # 2. Wind gusts, now and peak over the next 12 h
    gust_now = float(c.get("wind_gusts_10m") or 0.0)
    if gust_now >= th.gust_crit:
        push("gust-now", "critical", "Damaging wind gusts", f"{fmt_speed(gust_now, imperial)} right now.")
    elif gust_now >= th.gust_warn:
        push("gust-now", "warning", "Strong wind gusts", f"{fmt_speed(gust_now, imperial)} right now.")
    gust_ahead = max(_finite(h["wind_gusts_10m"][i + 1 : i + 13]), default=0.0)
    if gust_ahead >= th.gust_crit and gust_now < th.gust_crit:
        push(
            "gust-ahead",
            "warning",
            "Damaging gusts expected",
            f"Up to {fmt_speed(gust_ahead, imperial)} within 12 hours.",
        )

    # 3. Heat and cold stress on apparent temperature
    feels = float(c["apparent_temperature"])
    if feels >= th.heat_crit:
        push("heat", "critical", "Extreme heat", f"Feels like {fmt_temp(feels, imperial)}. Heat illness risk is high.")
    elif feels >= th.heat_warn:
        push("heat", "warning", "Heat advisory", f"Feels like {fmt_temp(feels, imperial)}.")
    if feels <= th.cold_crit:
        push(
            "cold",
            "critical",
            "Extreme cold",
            f"Feels like {fmt_temp(feels, imperial)}. Frostbite risk within minutes.",
        )
    elif feels <= th.cold_warn:
        push("cold", "warning", "Cold advisory", f"Feels like {fmt_temp(feels, imperial)}.")

    # 4. Heavy rain within 6 h
    rain_ahead = max(_finite(h["precipitation"][i : i + 7]), default=0.0)
    if rain_ahead >= th.rain_crit:
        push("rain", "critical", "Flash flood potential", f"Up to {rain_ahead:.1f} mm in a single hour within 6 hours.")
    elif rain_ahead >= th.rain_warn:
        push("rain", "warning", "Heavy rain", f"Up to {rain_ahead:.1f} mm/h within 6 hours.")

    # 5. Pressure tendency over 3 h
    p_now, p_prev = _at(h["pressure_msl"], i), _at(h["pressure_msl"], i - 3)
    if p_now is not None and p_prev is not None:
        drop = p_prev - p_now
        if drop >= th.pres_drop_crit:
            push(
                "pres", "critical", "Pressure crashing", f"Down {drop:.1f} hPa in 3 hours. A strong system is arriving."
            )
        elif drop >= th.pres_drop_warn:
            push("pres", "warning", "Pressure falling fast", f"Down {drop:.1f} hPa in 3 hours.")

    # 6. Rapid temperature swing over 3 h
    t_now, t_prev = _at(h["temperature_2m"], i), _at(h["temperature_2m"], i - 3)
    if t_now is not None and t_prev is not None:
        jump = t_now - t_prev
        if abs(jump) >= th.temp_jump_warn:
            sign = "+" if jump > 0 else ""
            push(
                "swing",
                "warning",
                "Sudden warm-up" if jump > 0 else "Sudden temperature drop",
                f"{sign}{fmt_delta(jump, imperial)} in 3 hours.",
            )

    # 7. Statistical anomaly: current temperature against the trailing history
    hist = _finite(h["temperature_2m"][: past_days * 24])
    m = sd = z = None
    if len(hist) >= 48:
        m = mean(hist)
        sd = stdev(hist)
        if sd > 0.5:
            cur = float(c["temperature_2m"])
            z = (cur - m) / sd
            az = abs(z)
            direction = "warmer" if z > 0 else "colder"
            detail = (
                f"{fmt_temp(cur, imperial)} is {az:.1f} standard deviations {direction} than this week's "
                f"average of {fmt_temp(m, imperial)}."
            )
            if az >= th.z_crit:
                push("zscore", "critical", "Highly unusual temperature", detail)
            elif az >= th.z_warn:
                push("zscore", "warning", "Unusual temperature", detail)
            elif az >= th.z_watch:
                push("zscore", "watch", "Temperature outside normal range", detail)

    # 8. UV
    uv = float(c.get("uv_index") or 0.0)
    if uv >= th.uv_crit:
        push("uv", "warning", "Extreme UV", f"Index {uv:.1f}. Unprotected skin burns in minutes.")
    elif uv >= th.uv_warn:
        push("uv", "watch", "Very high UV", f"Index {uv:.1f}.")

    alerts.sort(key=lambda a: -LEVEL_RANK[a.level])
    score = 0.0
    for n, a in enumerate(alerts):
        score += WEIGHTS[a.level] * (1.0 if n == 0 else 0.35)
    level = alerts[0].level if alerts else "calm"

    return Analysis(
        alerts=alerts,
        score=min(100, round(score)),
        level=level,  # type: ignore[arg-type]
        idx=i,
        condition=desc,
        stats=Stats(mean=m, sd=sd, z=z),
    )
