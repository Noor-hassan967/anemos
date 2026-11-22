
const citySelect = document.getElementById("city-select");
const refreshButton = document.getElementById("refresh-btn");
const statusText = document.getElementById("status");

const aqiValue = document.getElementById("aqi-value");
const aqiCategory = document.getElementById("aqi-category");
const pm25Value = document.getElementById("pm25-value");
const pm10Value = document.getElementById("pm10-value");
const updatedAt = document.getElementById("updated-at");
const comparisonStatus = document.getElementById("comparison-status");
const comparisonScroll = document.getElementById("comparison-scroll");
const comparisonBody = document.getElementById("comparison-body");

const aqiLevels = [
    { max: 50, category: "Good", color: "#00e400" },
    { max: 100, category: "Moderate", color: "#ffff00" },
    { max: 150, category: "Unhealthy for Sensitive Groups", color: "#ff7e00" },
    { max: 200, category: "Unhealthy", color: "#ff0000" },
    { max: 300, category: "Very Unhealthy", color: "#8f3f97" },
    { max: Infinity, category: "Hazardous", color: "#7e0023" }
];
const unavailableAQI = { category: "Unavailable", color: "#808080" };
const mapMarkers = new Map();
let comparisonAQI = new Map();

function getAQILevel(aqi) {
    if (aqi === null || aqi === undefined) return unavailableAQI;
    return aqiLevels.find(level => aqi <= level.max) ?? unavailableAQI;
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
        aqiCategory.textContent = getAQILevel(current.us_aqi).category;
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

let comparisonLoading = false;

async function loadCityComparison() {
    if (comparisonLoading) return;

    comparisonLoading = true;
    comparisonStatus.textContent = "Loading city comparisons...";

    try {
        const response = await fetch("/api/compare");

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const data = await response.json();
        if (!Array.isArray(data?.cities) || data.cities.length === 0) {
            throw new Error("City comparison data is unavailable");
        }

        const rows = document.createDocumentFragment();
        const aqiByCity = new Map();
        for (const { city, aqi, pm25, pm10 } of data.cities) {
            const reading = Number.isFinite(aqi) && aqi >= 0 ? aqi : null;
            aqiByCity.set(city, reading);

            const row = document.createElement("tr");
            const cityCell = document.createElement("th");
            cityCell.scope = "row";
            cityCell.textContent = displayValue(city);
            row.append(cityCell);

            const values = [reading, getAQILevel(reading).category, pm25, pm10];
            for (const value of values) {
                const cell = row.insertCell();
                cell.textContent = displayValue(value);
            }

            rows.append(row);
        }

        comparisonAQI = aqiByCity;
        comparisonBody.replaceChildren(rows);
        comparisonScroll.hidden = false;
        comparisonStatus.textContent = "";
    } catch (error) {
        comparisonAQI.clear();
        comparisonScroll.hidden = true;
        comparisonStatus.textContent = "Unable to load city comparisons. Select Refresh Data to try again.";
        console.error("City comparison request failed:", error);
    } finally {
        comparisonLoading = false;
    }

    updateMapMarkers();
}

function updateMapMarkers() {
    for (const [city, marker] of mapMarkers) {
        const aqi = comparisonAQI.get(city);
        const { category, color } = getAQILevel(aqi);

        const popup = document.createElement("div");
        const name = document.createElement("strong");
        name.textContent = city;
        const reading = document.createElement("div");
        reading.textContent = `US AQI: ${displayValue(aqi)}`;
        const description = document.createElement("div");
        description.textContent = `Category: ${category}`;
        popup.append(name, reading, description);

        marker.setStyle({ fillColor: color });
        marker.setPopupContent(popup);
    }
}

async function initializeMap() {
    try {
        const map = L.map("map", {
            scrollWheelZoom: false
        }).setView([30.4, 69.3], 5);

        L.tileLayer(
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
                maxZoom: 18
            }
        ).addTo(map);

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
                    fillColor: unavailableAQI.color,
                    fillOpacity: 0.9
                }
            ).addTo(map);

            marker.bindPopup(document.createElement("div"));
            mapMarkers.set(city.name, marker);

            marker.on("click", () => {
                citySelect.value = city.name;
                loadAirQuality();
            });
        });

        updateMapMarkers();

    } catch (error) {
        console.error("Map initialization failed:", error);
        statusText.textContent = "City markers could not be loaded.";
    }
}

citySelect.addEventListener("change", loadAirQuality);
refreshButton.addEventListener("click", () => {
    loadAirQuality();
    loadCityComparison();
});

loadAirQuality();
loadCityComparison();
initializeMap();

