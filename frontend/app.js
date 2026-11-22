
const citySelect = document.getElementById("city-select");
const refreshButton = document.getElementById("refresh-btn");
const statusText = document.getElementById("status");

const aqiValue = document.getElementById("aqi-value");
const aqiCategory = document.getElementById("aqi-category");
const pm25Value = document.getElementById("pm25-value");
const pm10Value = document.getElementById("pm10-value");
const updatedAt = document.getElementById("updated-at");

function getAQICategory(aqi) {
    if (aqi === null || aqi === undefined) return "Unavailable";
    if (aqi <= 50) return "Good";
    if (aqi <= 100) return "Moderate";
    if (aqi <= 150) return "Unhealthy for Sensitive Groups";
    if (aqi <= 200) return "Unhealthy";
    if (aqi <= 300) return "Very Unhealthy";
    return "Hazardous";
}

function displayValue(value) {
    return value === null || value === undefined ? "N/A" : String(value);
}

let aqiChart = null;

function renderAQIChart(hourly, currentTime) {
    if (typeof Chart === "undefined" || !hourly?.time || !hourly?.us_aqi) {
        console.error("Chart library or hourly data unavailable");
        return;
    }

    // Find the first forecast hour
    const index = hourly.time.findIndex(time => time >= currentTime);
    const start = index === -1 ? 0 : index;

    // Select the next 24 hours
    const times = hourly.time.slice(start, start + 24);
    const values = hourly.us_aqi.slice(start, start + 24);

    const labels = times.map(time =>
        `${time.slice(5, 10)} ${time.slice(11, 16)}`
    );

    // Remove the previous chart when switching cities
    if (aqiChart) {
        aqiChart.destroy();
    }

    aqiChart = new Chart(document.getElementById("aqi-chart"), {
        type: "line",
        data: {
            labels: labels,
            datasets: [{
                label: "US AQI",
                data: values,
                borderColor: "#34d399",
                backgroundColor: "rgba(52, 211, 153, 0.12)",
                fill: true,
                tension: 0.35,
                pointRadius: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: { display: false }
            },
            scales: {
                x: {
                    ticks: { color: "#94a3b8", maxTicksLimit: 6 },
                    grid: { display: false }
                },
                y: {
                    beginAtZero: true,
                    ticks: { color: "#94a3b8" },
                    grid: { color: "rgba(148,163,184,0.12)" }
                }
            }
        }
    });
}


async function loadAirQuality() {
    const city = citySelect.value;

    statusText.textContent = `Loading data for ${city}...`;
    refreshButton.disabled = true;

    try {
        const response = await fetch(
            `/api/air-quality?city=${encodeURIComponent(city)}`
        );

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const data = await response.json();
        const current = data.current;

        if (!current || !current.time) {
            throw new Error("Current air-quality data is unavailable");
        }

        aqiValue.textContent = displayValue(current.us_aqi);
        aqiCategory.textContent = getAQICategory(current.us_aqi);
        pm25Value.textContent = displayValue(current.pm2_5);
        pm10Value.textContent = displayValue(current.pm10);

        updatedAt.textContent = `Data time: ${current.time} (Pakistan time)`;
        renderAQIChart(data.hourly, current.time);
        statusText.textContent = `Showing latest available estimates for ${city}`;

    } catch (error) {
        statusText.textContent = `Unable to load ${city}. Please try again.`;
        aqiValue.textContent = "--";
        aqiCategory.textContent = "Unavailable";
        pm25Value.textContent = "--";
        pm10Value.textContent = "--";
        updatedAt.textContent = "Data time: Unavailable";
        console.error("Air quality request failed:", error);
    } finally {
        refreshButton.disabled = false;
    }
}

citySelect.addEventListener("change", loadAirQuality);
refreshButton.addEventListener("click", loadAirQuality);

loadAirQuality();


async function initializeMap() {
    const map = L.map("map", {
        scrollWheelZoom: false
    }).setView([30.4, 69.3], 5);

    L.tileLayer(
        "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
            attribution: '&copy; OpenStreetMap contributors',
            maxZoom: 18
        }
    ).addTo(map);

    try {
        const response = await fetch("/api/cities");

        if (!response.ok) {
            throw new Error("Failed to load cities");
        }

        const cities = await response.json();

        cities.forEach(city => {
            const marker = L.circleMarker(
                [city.latitude, city.longitude],
                {
                    radius: 9,
                    color: "#ffffff",
                    weight: 2,
                    fillColor: "#34d399",
                    fillOpacity: 0.9
                }
            ).addTo(map);

            marker.bindPopup(
                document.createTextNode(city.name)
            );

            marker.on("click", () => {
                citySelect.value = city.name;
                loadAirQuality();
            });
        });

    } catch (error) {
        console.error("Map initialization failed:", error);
        statusText.textContent = "City markers could not be loaded.";
    }
}

initializeMap();

