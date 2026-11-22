import asyncio
import re
from collections import deque

import httpx
import pytest
from httpx import AsyncClient

from backend import main, provider_cache, security
from backend.cities import CITIES


pytestmark = pytest.mark.anyio

CITY_NAMES = (
    "Peshawar", "Lahore", "Karachi", "Islamabad", "Quetta", "Multan", "Faisalabad"
)
AIR_QUALITY = "/api/air-quality"
COMPARE = "/api/compare"
PROVIDER_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_QUALITY_ERROR = "Air quality data is temporarily unavailable"
COMPARE_ERROR = "City comparison data is unavailable"
CURRENT = {"time": "2026-11-22T12:00", "us_aqi": 83, "pm2_5": 24.5, "pm10": 41}
HOURLY = {"time": ["2026-11-22T12:00", "2026-11-22T13:00"], "us_aqi": [83, 79]}


@pytest.fixture
def provider(monkeypatch):
    responses = deque()
    requests = []

    async def handle_request(request):
        requests.append(request)
        if not responses:
            pytest.fail(f"Unexpected outbound request: {request.method} {request.url}")
        response = responses.popleft()
        if isinstance(response, httpx.RequestError):
            raise response
        if callable(response):
            return await response(request)
        return response

    def mock_client(**options):
        return AsyncClient(transport=httpx.MockTransport(handle_request), **options)

    monkeypatch.setattr(main.httpx, "AsyncClient", mock_client)
    return responses, requests


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def client(provider):
    app = main.create_app()
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://testserver") as test_client:
            yield test_client


async def test_health(client):
    response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "application": "Anemos"}


async def test_cities(client):
    response = await client.get("/api/cities")

    assert response.status_code == 200
    assert response.json() == [{"name": city, **CITIES[city]} for city in CITY_NAMES]


@pytest.mark.parametrize(
    ("query", "city"),
    [(city, city) for city in CITY_NAMES] + [("  lAhOrE  ", "Lahore"), (None, "Peshawar")],
)
async def test_air_quality_for_supported_city(client, provider, query, city):
    responses, requests = provider
    responses.append(httpx.Response(200, json={"current": CURRENT, "hourly": HOURLY}))

    response = await client.get(AIR_QUALITY, params={} if query is None else {"city": query})

    assert response.status_code == 200
    assert response.json() == {
        "city": city,
        "source": "Open-Meteo CAMS Global (modeled estimates)",
        "current": CURRENT,
        "hourly": HOURLY,
    }
    request, = requests
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == PROVIDER_URL
    assert dict(request.url.params) == {
        "latitude": str(CITIES[city]["latitude"]),
        "longitude": str(CITIES[city]["longitude"]),
        "current": "us_aqi,pm2_5,pm10",
        "hourly": "us_aqi,pm2_5,pm10",
        "forecast_days": "2",
        "timezone": "Asia/Karachi",
        "domains": "cams_global",
    }


@pytest.mark.parametrize("city", ["London", "", "   "])
async def test_unsupported_city_does_not_call_provider(client, provider, city):
    _, requests = provider

    response = await client.get(AIR_QUALITY, params={"city": city})

    assert response.status_code == 400
    assert response.json() == {"detail": "City not supported"}
    assert not requests


async def test_compare_batches_all_cities_in_one_request(client, provider):
    responses, requests = provider
    readings = [{**CURRENT, "us_aqi": index * 25} for index in range(len(CITY_NAMES))]
    responses.append(httpx.Response(200, json=[{"current": reading} for reading in readings]))

    response = await client.get(COMPARE)

    assert response.status_code == 200
    assert response.json() == {
        "source": "Open-Meteo CAMS Global",
        "cities": [
            {
                "city": city,
                "aqi": reading["us_aqi"],
                "pm25": reading["pm2_5"],
                "pm10": reading["pm10"],
                "time": reading["time"],
            }
            for city, reading in zip(CITY_NAMES, readings, strict=True)
        ],
    }
    request, = requests
    assert request.method == "GET"
    assert str(request.url.copy_with(query=None)) == PROVIDER_URL
    assert dict(request.url.params) == {
        "latitude": ",".join(str(CITIES[city]["latitude"]) for city in CITY_NAMES),
        "longitude": ",".join(str(CITIES[city]["longitude"]) for city in CITY_NAMES),
        "current": "us_aqi,pm2_5,pm10",
        "timezone": "Asia/Karachi",
        "domains": "cams_global",
    }


@pytest.mark.parametrize("payload", [{}, {"current": None, "hourly": None}])
async def test_air_quality_allows_missing_sections(client, provider, payload):
    responses, _ = provider
    responses.append(httpx.Response(200, json=payload))

    response = await client.get(AIR_QUALITY)

    assert response.status_code == 200
    assert response.json()["current"] == {}
    assert response.json()["hourly"] == {}


async def test_air_quality_preserves_partial_readings(client, provider):
    responses, _ = provider
    current = {"time": CURRENT["time"], "us_aqi": None, "pm2_5": 0}
    responses.append(httpx.Response(200, json={"current": current}))

    response = await client.get(AIR_QUALITY)

    assert response.status_code == 200
    assert response.json()["current"] == current
    assert response.json()["hourly"] == {}


async def test_compare_preserves_cities_with_missing_or_partial_readings(client, provider):
    responses, _ = provider
    payload = [{}, {"current": None}, {"current": {"us_aqi": 0, "pm10": None}}]
    payload += [{"current": CURRENT}] * (len(CITY_NAMES) - len(payload))
    responses.append(httpx.Response(200, json=payload))

    response = await client.get(COMPARE)

    assert response.status_code == 200
    cities = response.json()["cities"]
    assert [row["city"] for row in cities] == list(CITY_NAMES)
    assert cities[:3] == [
        {"city": "Peshawar", "aqi": None, "pm25": None, "pm10": None, "time": None},
        {"city": "Lahore", "aqi": None, "pm25": None, "pm10": None, "time": None},
        {"city": "Karachi", "aqi": 0, "pm25": None, "pm10": None, "time": None},
    ]


@pytest.mark.parametrize(
    ("path", "detail"),
    [(AIR_QUALITY, AIR_QUALITY_ERROR), (COMPARE, COMPARE_ERROR)],
    ids=["air-quality", "compare"],
)
@pytest.mark.parametrize(
    "failure", ["timeout", "connection-error", "rate-limit", "bad-gateway", "server-error", "redirect", "invalid-json"]
)
async def test_provider_failures_return_generic_error(client, provider, path, detail, failure, caplog):
    responses, requests = provider
    failures = {
        "timeout": (httpx.ReadTimeout("Private upstream timeout details"), "ReadTimeout", None),
        "connection-error": (httpx.ConnectError("Private upstream connection details"), "ConnectError", None),
        "rate-limit": (httpx.Response(429, text="Private upstream rate limit details"), "HTTPStatusError", 429),
        "bad-gateway": (httpx.Response(502, text="Private upstream gateway details"), "HTTPStatusError", 502),
        "server-error": (httpx.Response(503, text="Private upstream error details"), "HTTPStatusError", 503),
        "redirect": (httpx.Response(302, headers={"Location": "https://example.invalid/"}), "HTTPStatusError", 302),
        "invalid-json": (
            httpx.Response(200, content=b"<html>Private upstream response</html>"), "JSONDecodeError", None
        ),
    }
    provider_response, error_type, upstream_status = failures[failure]
    responses.append(provider_response)

    response = await client.get(path)

    assert response.status_code == 502
    assert response.json() == {"detail": detail}
    assert len(requests) == 1
    if path == COMPARE:
        warning, = [record for record in caplog.records if record.name == "backend.main"]
        assert warning.levelname == "WARNING"
        assert warning.exc_info is None
        assert re.fullmatch(
            rf"Open-Meteo comparison failed stage=fetch error={error_type} upstream_status={upstream_status} "
            rf"expected_records={len(CITY_NAMES)} received_records=None elapsed_ms=\d+",
            warning.getMessage(),
        )
        assert "Private upstream" not in caplog.text
        assert PROVIDER_URL not in caplog.text
        assert "example.invalid" not in caplog.text


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"[]", id="array"),
        pytest.param(b"null", id="null"),
        pytest.param(b'{"current": []}', id="invalid-current"),
        pytest.param(b'{"hourly": "invalid"}', id="invalid-hourly"),
        pytest.param(b'{"current": {"us_aqi": NaN}}', id="nan"),
        pytest.param(b'{"hourly": {"us_aqi": [1e400]}}', id="float-overflow"),
    ],
)
async def test_air_quality_rejects_malformed_provider_payload(client, provider, content):
    responses, _ = provider
    responses.append(httpx.Response(200, content=content))

    response = await client.get(AIR_QUALITY)

    assert response.status_code == 502
    assert response.json() == {"detail": AIR_QUALITY_ERROR}


@pytest.mark.parametrize(
    ("payload", "stage", "received_records"),
    [
        pytest.param({"current": CURRENT}, "batch_shape", None, id="object"),
        pytest.param([], "batch_shape", 0, id="empty-array"),
        pytest.param([{}] * 6, "batch_shape", 6, id="too-few-records"),
        pytest.param([{}] * 8, "batch_shape", 8, id="too-many-records"),
        pytest.param([None] + [{}] * 6, "readings", 7, id="invalid-record"),
        pytest.param([{"current": []}] + [{}] * 6, "readings", 7, id="invalid-current"),
        pytest.param(
            [{"error": True, "reason": "Private upstream details"}] + [{}] * 6, "readings", 7, id="error-record"
        ),
    ],
)
async def test_compare_rejects_malformed_provider_payload(client, provider, payload, stage, received_records, caplog):
    responses, _ = provider
    responses.append(httpx.Response(200, json=payload))

    response = await client.get(COMPARE)

    assert response.status_code == 502
    assert response.json() == {"detail": COMPARE_ERROR}
    warning, = [record for record in caplog.records if record.name == "backend.main"]
    assert warning.exc_info is None
    assert re.fullmatch(
        rf"Open-Meteo comparison failed stage={stage} error=ValueError upstream_status=None "
        rf"expected_records={len(CITY_NAMES)} received_records={received_records} elapsed_ms=\d+",
        warning.getMessage(),
    )
    assert "Private upstream" not in caplog.text


def _provider_reading(path, aqi):
    current = {**CURRENT, "us_aqi": aqi}
    if path == COMPARE:
        return [{"current": current} for _ in CITY_NAMES]
    return {"current": current, "hourly": HOURLY}


@pytest.mark.parametrize("path", [AIR_QUALITY, COMPARE])
async def test_cache_expires_ten_minutes_after_fetch_completes(client, provider, monkeypatch, path):
    responses, requests = provider
    now = 100.0
    monkeypatch.setattr(provider_cache, "monotonic", lambda: now)

    async def first_reading(request):
        nonlocal now
        now += 10
        return httpx.Response(200, json=_provider_reading(path, 83))

    responses.extend([first_reading, httpx.Response(200, json=_provider_reading(path, 97))])

    first = await client.get(path)
    now += 599
    cached = await client.get(path)
    assert first.status_code == cached.status_code == 200
    assert cached.json() == first.json()
    assert len(requests) == 1

    now += 1
    renewed = await client.get(path)
    assert renewed.status_code == 200
    assert renewed.json() != first.json()
    assert len(requests) == 2


async def test_cache_keys_use_normalized_city_and_ignore_unrelated_query_parameters(client, provider):
    responses, requests = provider
    responses.extend([
        httpx.Response(200, json=_provider_reading(AIR_QUALITY, 83)),
        httpx.Response(200, json=_provider_reading(AIR_QUALITY, 97)),
        httpx.Response(200, json=_provider_reading(COMPARE, 104)),
    ])

    default = await client.get(AIR_QUALITY)
    normalized = await client.get(AIR_QUALITY, params={"city": "  peSHaWar  ", "extra": "value"})
    other_city = await client.get(AIR_QUALITY, params={"city": "Lahore"})
    compare = await client.get(COMPARE)
    compare_with_query = await client.get(COMPARE, params={"city": "Lahore"})

    assert default.json() == normalized.json()
    assert other_city.json()["current"]["us_aqi"] == 97
    assert compare.json() == compare_with_query.json()
    assert compare.json()["cities"][0]["aqi"] == 104
    assert len(requests) == 3


@pytest.mark.parametrize("path", [AIR_QUALITY, COMPARE])
@pytest.mark.parametrize("provider_status", [200, 503])
async def test_concurrent_requests_share_a_fetch_and_its_outcome(client, provider, path, provider_status, caplog):
    responses, requests = provider
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(request):
        started.set()
        await release.wait()
        return httpx.Response(provider_status, json=_provider_reading(path, 83))

    responses.append(fetch)

    async with asyncio.timeout(3), asyncio.TaskGroup() as group:
        first = group.create_task(client.get(path))
        await started.wait()
        second = group.create_task(client.get(path))
        await asyncio.sleep(0)
        assert not second.done()
        release.set()

    expected_status = 200 if provider_status == 200 else 502
    assert first.result().status_code == second.result().status_code == expected_status
    assert first.result().json() == second.result().json()
    assert len(requests) == 1
    warnings = [record for record in caplog.records if record.name == "backend.main"]
    assert len(warnings) == int(path == COMPARE and provider_status == 503)


@pytest.mark.parametrize(
    ("path", "malformed"),
    [
        pytest.param(AIR_QUALITY, {"error": True, "reason": "Provider error"}, id="error-record"),
        pytest.param(AIR_QUALITY, {"hourly": {"time": HOURLY["time"], "us_aqi": [83]}}, id="hourly-length"),
        pytest.param(AIR_QUALITY, {"hourly": {"time": "invalid", "us_aqi": [83]}}, id="hourly-time-type"),
        pytest.param(COMPARE, [{"current": {"us_aqi": "invalid"}}] * len(CITY_NAMES), id="compare-aqi-type"),
    ],
)
async def test_malformed_provider_results_are_not_cached(client, provider, path, malformed):
    responses, requests = provider
    responses.extend([
        httpx.Response(200, json=malformed),
        httpx.Response(200, json=_provider_reading(path, 83)),
    ])

    assert (await client.get(path)).status_code == 502
    valid = await client.get(path)
    assert valid.status_code == 200
    assert (await client.get(path)).json() == valid.json()
    assert len(requests) == 2


@pytest.mark.parametrize("path", [AIR_QUALITY, COMPARE])
async def test_provider_error_does_not_poison_cache(client, provider, path):
    responses, requests = provider
    responses.extend([httpx.Response(503), httpx.Response(200, json=_provider_reading(path, 83))])

    assert (await client.get(path)).status_code == 502
    valid = await client.get(path)
    assert valid.status_code == 200
    assert (await client.get(path)).json() == valid.json()
    assert len(requests) == 2


async def test_compare_upstream_error_can_surface_after_cache_expires(client, provider, monkeypatch):
    responses, requests = provider
    now = 100.0
    monkeypatch.setattr(provider_cache, "monotonic", lambda: now)
    responses.extend([
        httpx.Response(200, json=_provider_reading(COMPARE, 83)),
        httpx.Response(502),
        httpx.Response(200, json=_provider_reading(COMPARE, 97)),
    ])

    first = await client.get(COMPARE)
    assert first.status_code == 200
    now += provider_cache.CACHE_TTL_SECONDS - 1
    assert (await client.get(COMPARE)).json() == first.json()
    assert len(requests) == 1

    now += 1
    failed = await client.get(COMPARE)
    assert failed.status_code == 502
    assert failed.json() == {"detail": COMPARE_ERROR}
    recovered = await client.get(COMPARE)
    assert recovered.status_code == 200
    assert recovered.json()["cities"][0]["aqi"] == 97
    assert len(requests) == 3


async def test_outbound_limit_is_shared_and_cache_hits_bypass_it(client, provider):
    responses, requests = provider
    responses.append(httpx.Response(200, json=_provider_reading(AIR_QUALITY, 83)))
    cached = await client.get(AIR_QUALITY)
    release = asyncio.Event()
    at_capacity = asyncio.Event()

    async def fetch(request):
        if len(requests) == 4:
            at_capacity.set()
        await release.wait()
        path = AIR_QUALITY if "hourly" in request.url.params else COMPARE
        return httpx.Response(200, json=_provider_reading(path, 97))

    responses.extend([fetch] * 3)
    async with asyncio.timeout(4), asyncio.TaskGroup() as group:
        active = [
            group.create_task(client.get(AIR_QUALITY, params={"city": city}))
            for city in ("Lahore", "Karachi")
        ]
        active.append(group.create_task(client.get(COMPARE)))
        await at_capacity.wait()

        busy = await client.get(AIR_QUALITY, params={"city": "Quetta"})
        assert busy.status_code == 503
        assert busy.headers["Retry-After"] == "1"
        assert busy.json() == {"detail": "Air quality service is busy. Please try again."}
        assert (await client.get(AIR_QUALITY)).json() == cached.json()
        assert len(requests) == 4
        release.set()

    assert [call.result().status_code for call in active] == [200, 200, 200]
    responses.append(httpx.Response(200, json=_provider_reading(AIR_QUALITY, 97)))
    assert (await client.get(AIR_QUALITY, params={"city": "Quetta"})).status_code == 200
    assert len(requests) == 5


@pytest.mark.parametrize(
    ("path", "timeout_setting"),
    [(AIR_QUALITY, "AIR_QUALITY_TIMEOUT_SECONDS"), (COMPARE, "COMPARE_TIMEOUT_SECONDS")],
)
async def test_provider_deadline_allows_retry(client, provider, monkeypatch, path, timeout_setting, caplog):
    responses, requests = provider
    never_ready = asyncio.Event()

    async def stall(request):
        await never_ready.wait()

    monkeypatch.setattr(main, timeout_setting, 0.05)
    responses.extend([stall, httpx.Response(200, json=_provider_reading(path, 83))])

    assert (await asyncio.wait_for(client.get(path), timeout=2)).status_code == 502
    assert (await client.get(path)).status_code == 200
    assert len(requests) == 2
    if path == COMPARE:
        warning, = [record for record in caplog.records if record.name == "backend.main"]
        assert "stage=fetch error=TimeoutError " in warning.getMessage()


async def test_cancelled_caller_does_not_cancel_shared_fetch(client, provider):
    responses, requests = provider
    started = asyncio.Event()
    release = asyncio.Event()

    async def fetch(request):
        started.set()
        await release.wait()
        return httpx.Response(200, json=_provider_reading(COMPARE, 83))

    responses.append(fetch)
    async with asyncio.timeout(3), asyncio.TaskGroup() as group:
        first = group.create_task(client.get(COMPARE))
        await started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        second = group.create_task(client.get(COMPARE))
        await asyncio.sleep(0)
        release.set()

    assert second.result().status_code == 200
    assert len(requests) == 1


@pytest.mark.parametrize("path", ["/", "/app.js", "/style.css", "/api/health", "/unlisted"])
async def test_security_headers_cover_dashboard_assets_api_and_not_found(client, path):
    response = await client.get(path)

    assert response.status_code == (404 if path == "/unlisted" else 200)
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert response.headers["permissions-policy"] == "camera=(), microphone=(), geolocation=()"
    policy = response.headers["content-security-policy"]
    assert "default-src 'none'" in policy
    assert "connect-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert "https://tile.openstreetmap.org" in policy
    assert "'unsafe-inline'" not in policy
    assert "'unsafe-eval'" not in policy


async def test_external_dashboard_assets_have_integrity_and_are_allowed_by_csp(client):
    response = await client.get("/")
    tags = re.findall(r'<(?:link|script)\b[^>]+https://cdn\.jsdelivr\.net[^>]+>', response.text)

    assert len(tags) == 3
    for tag in tags:
        url = re.search(r'https://cdn\.jsdelivr\.net/[^"<>]+', tag).group()
        assert url in response.headers["content-security-policy"]
        assert re.search(r'integrity="sha384-[A-Za-z0-9+/]{64}"', tag)
        assert 'crossorigin="anonymous"' in tag


@pytest.mark.parametrize("path", ["/docs", "/docs/oauth2-redirect", "/redoc", "/openapi.json"])
async def test_api_docs_are_disabled_by_default(client, path):
    assert (await client.get(path)).status_code == 404


async def test_local_docs_opt_in_keeps_debug_disabled():
    app = main.create_app(enable_api_docs=True)
    transport = httpx.ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as docs_client:
        docs = await docs_client.get("/docs")
        schema = await docs_client.get("/openapi.json")
        dashboard = await docs_client.get("/")

    assert not app.debug
    assert docs.status_code == schema.status_code == dashboard.status_code == 200
    assert schema.json()["info"]["title"] == "Anemos API"
    assert AIR_QUALITY in schema.json()["paths"]
    assert "content-security-policy" in dashboard.headers
    assert docs.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("path", ["/backend/main.py", "/requirements.txt", "/.git/config", "/.env"])
async def test_project_files_outside_frontend_are_not_served(client, path):
    assert (await client.get(path)).status_code == 404


async def test_static_mount_excludes_symlinks_outside_frontend(tmp_path, monkeypatch):
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    private_file = tmp_path / "private.txt"
    private_file.write_text("Private file")
    (frontend / "linked.txt").symlink_to(private_file)
    monkeypatch.setattr(main, "FRONTEND_DIR", frontend)

    transport = httpx.ASGITransport(app=main.create_app())
    async with AsyncClient(transport=transport, base_url="http://testserver") as static_client:
        response = await static_client.get("/linked.txt")

    assert response.status_code == 404


async def test_untrusted_origins_are_not_given_cors_access(client):
    headers = {"Origin": "https://unrelated.example"}
    response = await client.get("/api/health", headers=headers)
    preflight = await client.options(AIR_QUALITY, headers={**headers, "Access-Control-Request-Method": "GET"})

    assert response.status_code == 200
    assert preflight.status_code == 405
    assert "access-control-allow-origin" not in response.headers
    assert "access-control-allow-origin" not in preflight.headers


async def test_unexpected_failure_returns_generic_500_with_security_headers(monkeypatch):
    async def fail(city):
        raise RuntimeError("Private internal detail")

    monkeypatch.setattr(main, "_load_air_quality", fail)
    app = main.create_app()
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=transport, base_url="http://testserver") as error_client:
            response = await error_client.get(AIR_QUALITY)

    assert response.status_code == 500
    assert response.text == "Internal Server Error"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "content-security-policy" in response.headers


async def test_worker_rate_limit_counts_cache_hits_and_recovers(client, provider, monkeypatch):
    responses, requests = provider
    responses.append(httpx.Response(200, json=_provider_reading(AIR_QUALITY, 83)))
    now = security.monotonic()
    monkeypatch.setattr(security, "monotonic", lambda: now)

    for _ in range(60):
        assert (await client.get(AIR_QUALITY)).status_code == 200

    for path in (AIR_QUALITY, f"{AIR_QUALITY}/", COMPARE, "/api/cities"):
        blocked = await client.get(path, headers={"X-Forwarded-For": "192.0.2.1"})
        assert blocked.status_code == 429
        assert blocked.headers["retry-after"] == "1"
        assert blocked.headers["cache-control"] == "no-store"
        assert blocked.headers["x-content-type-options"] == "nosniff"
        assert blocked.json() == {"detail": "Too many API requests. Please try again shortly."}

    assert (await client.get("/")).status_code == 200
    assert (await client.get("/api/health")).status_code == 200
    now += 1
    for _ in range(20):
        assert (await client.get(AIR_QUALITY)).status_code == 200
    assert (await client.get(AIR_QUALITY)).status_code == 429
    assert len(requests) == 1
