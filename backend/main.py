from fastapi.staticfiles import StaticFiles
import httpx
from fastapi import FastAPI, HTTPException
from backend.cities import CITIES

app = FastAPI(title="PakAir API")


@app.get("/api/health")
def health_check():
    return {"status": "ok", "application": "PakAir"}


@app.get("/api/air-quality")
async def get_air_quality(city: str = "Peshawar"):

    city_name = city.strip().title()

    if city_name not in CITIES:
        raise HTTPException(
            status_code=400,
            detail="City not supported"
        )

    coordinates = CITIES[city_name]

    url = "https://air-quality-api.open-meteo.com/v1/air-quality"

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
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            data = response.json()

    except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Air quality data is temporarily unavailable"
        )

    return {
        "city": city_name,
        "source": "Open-Meteo CAMS Global (modeled estimates)",
        "current": data.get("current", {}),
        "hourly": data.get("hourly", {}),
    }
@app.get("/api/cities")
def get_cities():
    return [
        {"name": name, **coordinates}
        for name, coordinates in CITIES.items()
    ]

@app.get("/api/compare")
async def compare_cities():
    names = list(CITIES.keys())

    params = {
        "latitude": ",".join(str(CITIES[name]["latitude"]) for name in names),
        "longitude": ",".join(str(CITIES[name]["longitude"]) for name in names),
        "current": "us_aqi,pm2_5,pm10",
        "timezone": "Asia/Karachi",
        "domains": "cams_global",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://air-quality-api.open-meteo.com/v1/air-quality",
                params=params
            )
            response.raise_for_status()
            data = response.json()

    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
        raise HTTPException(
            status_code=502,
            detail="City comparison data is unavailable"
        ) from exc

    if not isinstance(data, list) or len(data) != len(names):
        raise HTTPException(
            status_code=502,
            detail="Unexpected data from air quality provider"
        )

    results = []

    for name, record in zip(names, data):
        current = record.get("current", {})

        results.append({
            "city": name,
            "aqi": current.get("us_aqi"),
            "pm25": current.get("pm2_5"),
            "pm10": current.get("pm10"),
            "time": current.get("time"),
        })

    return {
        "source": "Open-Meteo CAMS Global",
        "cities": results
    }

app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")