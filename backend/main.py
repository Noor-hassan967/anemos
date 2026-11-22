import asyncio
import math
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles

from backend.cities import CITIES
from backend.provider_cache import ProviderCache

OPEN_METEO_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"
AIR_QUALITY_TIMEOUT_SECONDS = 15.0
COMPARE_TIMEOUT_SECONDS = 20.0
PROVIDER_FIELDS = {"time": (str,), "us_aqi": (int, float), "pm2_5": (int, float), "pm10": (int, float)}


@asynccontextmanager
async def _lifespan(app: FastAPI):
    cache = ProviderCache()
    app.state.provider_cache = cache
    try:
        yield
    finally:
        await cache.close()


app = FastAPI(title="PakAir API", lifespan=_lifespan)


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


@app.get("/api/health")
def health_check():
    return {"status": "ok", "application": "PakAir"}


@app.get("/api/air-quality")
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


@app.get("/api/cities")
def get_cities():
    return [
        {"name": name, **coordinates}
        for name, coordinates in CITIES.items()
    ]


@app.get("/api/compare")
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

    try:
        data = await _fetch_provider_data(params, timeout=COMPARE_TIMEOUT_SECONDS)
        if not isinstance(data, list) or len(data) != len(names):
            raise ValueError("Expected one provider record per city")

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
        raise HTTPException(
            status_code=502,
            detail="City comparison data is unavailable"
        ) from exc

    return {
        "source": "Open-Meteo CAMS Global",
        "cities": results
    }


app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")
