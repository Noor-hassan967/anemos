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
app.mount("/", StaticFiles(directory="frontend", html=True), name="frontend")