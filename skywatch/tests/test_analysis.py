from datetime import UTC, datetime, timedelta, timezone

from app.analysis import analyze, current_hour_index, fmt_temp
from tests.conftest import make_forecast, now_index

NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)


def test_calm_conditions_produce_no_alerts():
    res = analyze(make_forecast(now=NOW), now=NOW)
    assert res.alerts == []
    assert res.level == "calm"
    assert res.score == 0
    assert res.condition == "Mostly clear"


def test_extreme_heat_is_critical_and_formats_units():
    data = make_forecast(now=NOW, current={"apparent_temperature": 42.0})
    res = analyze(data, now=NOW)
    assert res.level == "critical"
    assert res.alerts[0].id == "heat"
    assert "42°C" in res.alerts[0].detail
    assert "108°F" in analyze(data, units="imperial", now=NOW).alerts[0].detail


def test_pressure_drop_over_three_hours():
    i = now_index(now=NOW)
    data = make_forecast(now=NOW, hourly_overrides={"pressure_msl": {i - 3: 1012.0, i: 1005.5}})
    res = analyze(data, now=NOW)
    ids = {a.id: a for a in res.alerts}
    assert ids["pres"].level == "critical"
    assert "6.5 hPa" in ids["pres"].detail


def test_incoming_thunderstorm_within_12h_is_a_warning_not_critical():
    i = now_index(now=NOW)
    data = make_forecast(now=NOW, hourly_overrides={"weather_code": {i + 5: 95}})
    res = analyze(data, now=NOW)
    assert any(a.id == "incoming-95" and a.level == "warning" for a in res.alerts)


def test_current_thunderstorm_is_critical():
    res = analyze(make_forecast(now=NOW, current={"weather_code": 95}), now=NOW)
    assert res.level == "critical"
    assert res.alerts[0].id == "code-95"


def test_zscore_flags_temperature_far_from_weekly_mean():
    # history sits around 25°C with a 3.6°C diurnal swing; 45°C now is many sigmas out
    data = make_forecast(now=NOW, current={"temperature_2m": 45.0, "apparent_temperature": 25.0})
    res = analyze(data, now=NOW)
    z = next(a for a in res.alerts if a.id == "zscore")
    assert z.level == "critical"
    assert res.stats.z is not None and res.stats.z > 3


def test_zscore_skipped_when_history_is_flat():
    data = make_forecast(now=NOW, current={"temperature_2m": 30.0, "apparent_temperature": 25.0})
    data["hourly"]["temperature_2m"] = [25.0] * len(data["hourly"]["temperature_2m"])  # sd == 0
    res = analyze(data, now=NOW)
    assert not any(a.id == "zscore" for a in res.alerts)


def test_score_caps_at_100_and_sorts_critical_first():
    i = now_index(now=NOW)
    data = make_forecast(
        now=NOW,
        current={"weather_code": 99, "wind_gusts_10m": 120.0, "apparent_temperature": 45.0, "uv_index": 12},
        hourly_overrides={"precipitation": {i + 2: 30.0}},
    )
    res = analyze(data, now=NOW)
    assert res.score == 100
    rank = {"critical": 0, "warning": 1, "watch": 2}
    levels = [a.level for a in res.alerts]
    assert levels == sorted(levels, key=rank.__getitem__)


def test_current_hour_index_respects_utc_offset():
    """Labels are location-local. The same UTC instant must land on different labels for different offsets."""
    at = datetime(2026, 10, 15, 14, 15, tzinfo=UTC)  # 14:15 UTC == 19:45 IST
    labels = make_forecast(now=at)["hourly"]["time"]
    utc_label = labels[current_hour_index(labels, 0, at)]
    ist_label = labels[current_hour_index(labels, 19_800, at)]
    assert utc_label.endswith("T14:00")
    assert ist_label.endswith("T20:00")
    assert timezone(timedelta(seconds=19_800)).utcoffset(None) == timedelta(hours=5, minutes=30)


def test_fmt_temp_rounds():
    assert fmt_temp(24.6, False) == "25°C"
    assert fmt_temp(0.0, True) == "32°F"
