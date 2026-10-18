import httpx

from tests.conftest import make_forecast


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_anomalies_returns_analysis_and_caches_upstream(client, mock_api):
    route = mock_api["forecast"]
    r1 = client.get("/api/anomalies", params={"lat": 28.61, "lon": 77.21, "name": "New Delhi"})
    r2 = client.get("/api/anomalies", params={"lat": 28.612, "lon": 77.214})  # rounds to the same cell
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["analysis"]["level"] == "calm"
    assert r1.json()["place"]["name"] == "New Delhi"
    assert route.call_count == 1
    assert client.get("/api/health").json()["cache_hits"] == 1


def test_anomalies_validates_coordinates(client):
    assert client.get("/api/anomalies", params={"lat": 123, "lon": 0}).status_code == 422
    assert client.get("/api/anomalies", params={"lat": 1, "lon": 0, "units": "kelvin"}).status_code == 422


def test_upstream_failure_maps_to_502(client, mock_api):
    mock_api["forecast"].mock(return_value=httpx.Response(503))
    r = client.get("/api/anomalies", params={"lat": 10, "lon": 10})
    assert r.status_code == 502
    assert "unavailable" in r.json()["detail"]
    assert "X-Request-ID" in r.headers


def test_geocode_proxies_and_shapes_results(client):
    r = client.get("/api/geocode", params={"q": "rajkot"})
    assert r.status_code == 200
    assert r.json()[0] == {
        "name": "Rajkot",
        "admin1": "Gujarat",
        "country": "India",
        "latitude": 22.3,
        "longitude": 70.8,
    }
    assert client.get("/api/geocode", params={"q": "r"}).status_code == 422


def test_india_places_lists_all_36_states_and_uts(client):
    places = client.get("/api/india/places").json()
    assert len(places) == 36
    assert places["Gujarat"][0]["name"] == "Gandhinagar"
    assert all(len(v) >= 1 for v in places.values())


def test_india_board_batches_one_upstream_call(client, mock_api):
    rows = [make_forecast(current={"apparent_temperature": 41.0 if k == 5 else 25.0}) for k in range(36)]
    route = mock_api["forecast"].mock(return_value=httpx.Response(200, json=rows))
    r = client.get("/api/india/board")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 36
    assert body[0]["analysis"]["level"] == "critical"  # sorted by score
    assert route.call_count == 1
    client.get("/api/india/board")
    assert route.call_count == 1  # cached


def test_rate_limit_returns_429_with_retry_after(client):
    codes = [client.get("/api/health").status_code for _ in range(8)]
    assert 429 in codes  # burst is 5 in the test settings
    r = client.get("/api/health")
    if r.status_code == 429:
        assert int(r.headers["Retry-After"]) >= 1


def test_static_index_served_at_root(client):
    r = client.get("/")
    assert r.status_code == 200 and "ok" in r.text


def test_unknown_api_path_is_404_not_static(client):
    assert client.get("/api/nope").status_code == 404
