import asyncio
import logging
import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from time import monotonic

import httpx
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles

from backend.cities import CITIES
from backend.provider_cache import ProviderCache
from backend.security import SECURITY_HEADERS, ApiRateLimiter, SecurityMiddleware

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
OPEN_METEO_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_QUALITY_TIMEOUT_SECONDS = 15.0
COMPARE_TIMEOUT_SECONDS = 20.0
PROVIDER_FIELDS = {"time": (str,), "us_aqi": (int, float), "pm2_5": (int, float), "pm10": (int, float)}
api = APIRouter()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    cache = ProviderCache()
    app.state.provider_cache = cache
    app.state.api_rate_limiter = ApiRateLimiter()
    try:
        yield
    finally:
        await cache.close()


async def _internal_error(_request: Request, _error: Exception) -> PlainTextResponse:
    return PlainTextResponse("Internal Server Error", status_code=500, headers=SECURITY_HEADERS)


def _finite_float(value: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("Provider numbers must be finite")
    return number


async def _fetch_provider_data(params: dict[str, str | float | int], timeout: float):
    async with asyncio.timeout(timeout), httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        response = await client.get(OPEN_METEO_URL, params=params)
        response.raise_for_status()
        return response.json(parse_float=_finite_float, parse_constant=_finite_float)


def _provider_section(record, key: str) -> dict:
    if not isinstance(record, dict):
        raise ValueError("Expected a provider record object")
    if record.get("error") is True:
        raise ValueError("Provider returned an error record")

    section = record.get(key)
    if section is None:
        return {}
    if not isinstance(section, dict):
        raise ValueError(f"Expected a provider {key} object")

    times = section.get("time")
    for field, allowed_types in PROVIDER_FIELDS.items():
        value = section.get(field)
        if value is None:
            continue

        values = (value,)
        if key == "hourly":
            if not isinstance(value, list) or (isinstance(times, list) and len(value) != len(times)):
                raise ValueError(f"Invalid provider hourly {field} array")
            values = value

        if any(type(item) not in allowed_types for item in values if item is not None or field == "time"):
            raise ValueError(f"Invalid provider {key} {field} value")

    return section


@api.get("/api/health")
def health_check():
    return {"status": "ok", "application": "PakAir"}


@api.get("/api/air-quality")
async def get_air_quality(request: Request, city: str = "Peshawar"):
    city_name = city.strip().title()

    if city_name not in CITIES:
        raise HTTPException(
            status_code=400,
            detail="City not supported"
        )

    return await request.app.state.provider_cache.get_or_load(city_name, lambda: _load_air_quality(city_name))


async def _load_air_quality(city_name: str) -> dict:
    coordinates = CITIES[city_name]

    params = {
        "latitude": coordinates["latitude"],
        "longitude": coordinates["longitude"],
        "current": "us_aqi,pm2_5,pm10",
        "hourly": "us_aqi,pm2_5,pm10",
        "forecast_days": 2,
        "timezone": "Asia/Karachi",
        "domains": "cams_global",
    }

    try:
        data = await _fetch_provider_data(params, timeout=AIR_QUALITY_TIMEOUT_SECONDS)
        current = _provider_section(data, "current")
        hourly = _provider_section(data, "hourly")
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="Air quality data is temporarily unavailable"
        ) from exc

    return {
        "city": city_name,
        "source": "Open-Meteo CAMS Global (modeled estimates)",
        "current": current,
        "hourly": hourly,
    }


@api.get("/api/cities")
def get_cities():
    return [
        {"name": name, **coordinates}
        for name, coordinates in CITIES.items()
    ]


@api.get("/api/compare")
async def compare_cities(request: Request):
    return await request.app.state.provider_cache.get_or_load("compare", _load_comparison)


async def _load_comparison() -> dict:
    names = list(CITIES.keys())

    params = {
        "latitude": ",".join(str(CITIES[name]["latitude"]) for name in names),
        "longitude": ",".join(str(CITIES[name]["longitude"]) for name in names),
        "current": "us_aqi,pm2_5,pm10",
        "timezone": "Asia/Karachi",
        "domains": "cams_global",
    }

    started_at = monotonic()
    stage = "fetch"
    received_records = None
    try:
        data = await _fetch_provider_data(params, timeout=COMPARE_TIMEOUT_SECONDS)
        stage = "batch_shape"
        received_records = len(data) if isinstance(data, list) else None
        if received_records != len(names):
            raise ValueError("Expected one provider record per city")

        stage = "readings"
        results = []
        for name, record in zip(names, data, strict=True):
            current = _provider_section(record, "current")
            results.append({
                "city": name,
                "aqi": current.get("us_aqi"),
                "pm25": current.get("pm2_5"),
                "pm10": current.get("pm10"),
                "time": current.get("time"),
            })
    except (httpx.HTTPError, TimeoutError, ValueError) as exc:
        upstream_status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
        logger.warning(
            "Open-Meteo comparison failed stage=%s error=%s upstream_status=%s "
            "expected_records=%d received_records=%s elapsed_ms=%d",
            stage, type(exc).__name__, upstream_status, len(names), received_records,
            round((monotonic() - started_at) * 1000),
        )
        raise HTTPException(
            status_code=502,
            detail="City comparison data is unavailable"
        ) from exc

    return {
        "source": "Open-Meteo CAMS Global",
        "cities": results
    }


def create_app(*, enable_api_docs: bool = False) -> FastAPI:
    application = FastAPI(
        title="PakAir API",
        debug=False,
        lifespan=_lifespan,
        openapi_url="/openapi.json" if enable_api_docs else None,
        exception_handlers={500: _internal_error},
    )
    application.add_middleware(SecurityMiddleware, docs_enabled=enable_api_docs)
    application.include_router(api)
    application.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True, follow_symlink=False), name="frontend")
    return application


app = create_app(enable_api_docs=os.getenv("PAKAIR_ENABLE_API_DOCS") == "1")
