
const citySelect = document.getElementById("city-select");
const refreshButton = document.getElementById("refresh-btn");
const statusText = document.getElementById("status");
const cityHeading = document.getElementById("current-heading");
const currentOverview = document.getElementById("current-overview");

const aqiValue = document.getElementById("aqi-value");
const aqiCategory = document.getElementById("aqi-category");
const aqiExplanation = document.getElementById("aqi-explanation");
const aqiScale = document.getElementById("aqi-scale");
const aqiScaleBands = document.getElementById("aqi-scale-bands");
const aqiScaleMarker = document.getElementById("aqi-scale-marker");
const aqiScaleNote = document.getElementById("aqi-scale-note");
const pm25Value = document.getElementById("pm25-value");
const pm10Value = document.getElementById("pm10-value");
const updatedAt = document.getElementById("updated-at");
const comparisonStatus = document.getElementById("comparison-status");
const comparisonScroll = document.getElementById("comparison-scroll");
const comparisonBody = document.getElementById("comparison-body");
const mapFrame = document.getElementById("map");
const mapKey = document.getElementById("map-key");
const mapStatus = document.getElementById("map-status");
const supportedCities = Array.from(citySelect.options, option => option.value);
const AQI_SCALE_MAX = 500;
const aqiLevels = [
    { max: 50, level: "good", category: "Good",
        explanation: "Air pollution poses little or no health risk." },
    { max: 100, level: "moderate", category: "Moderate",
        explanation: "Air quality is generally acceptable. People who are unusually sensitive may be affected." },
    { max: 150, level: "sensitive", category: "Unhealthy for Sensitive Groups",
        explanation: "People who are sensitive to air pollution are more likely to experience health effects." },
    { max: 200, level: "unhealthy", category: "Unhealthy",
        explanation: "Some people may experience health effects. Sensitive groups may be affected more severely." },
    { max: 300, level: "very-unhealthy", category: "Very Unhealthy",
        explanation: "The risk of health effects is higher for everyone." },
    { max: Infinity, level: "hazardous", category: "Hazardous",
        explanation: "At this level, health effects are more likely for everyone." }
];
const unavailableAQI = { level: "unavailable", category: "Unavailable",
    explanation: "An AQI estimate is needed to show a category." };
const mapMarkers = new Map();
const comparisonChoices = new Map();
let comparisonReadings = new Map();

function getAQILevel(aqi) {
    return isValidReading(aqi) ? aqiLevels.find(level => aqi <= level.max) : unavailableAQI;
}

function initializeAQIReference() {
    const scaleTicks = document.getElementById("aqi-scale-ticks");
    const guide = document.getElementById("aqi-guide-levels");
    const bandWidths = [];
    let lower = 0;

    for (const { max, level, category } of aqiLevels) {
        const band = document.createElement("span");
        band.dataset.level = level;
        aqiScaleBands.append(band);
        bandWidths.push(`${Math.min(max, AQI_SCALE_MAX) - lower}fr`);

        const item = document.createElement("div");
        const term = document.createElement("dt");
        term.className = "category-marker";
        term.dataset.level = level;
        term.textContent = category;
        const range = document.createElement("dd");
        const start = lower === 0 ? 0 : lower + 1;
        range.textContent = Number.isFinite(max) ? `${start}–${max}` : `${start}+`;
        item.append(term, range);
        guide.append(item);
        lower = max;
    }

    aqiScaleBands.style.gridTemplateColumns = bandWidths.join(" ");
    const tickValues = [0, ...aqiLevels.map(level => Math.min(level.max, AQI_SCALE_MAX))];
    for (const value of tickValues) {
        const label = document.createElement("span");
        label.textContent = value;
        label.style.insetInlineStart = `${value / AQI_SCALE_MAX * 100}%`;
        scaleTicks.append(label);
    }
}

function renderCurrentEstimate(current = {}) {
    const { us_aqi: aqi, pm2_5: pm25, pm10, time } = current;
    const { category, level, explanation } = getAQILevel(aqi);
    const available = level !== unavailableAQI.level;
    const overflow = available && aqi > AQI_SCALE_MAX;
    aqiValue.textContent = available ? String(aqi) : "—";
    aqiCategory.textContent = category;
    aqiCategory.dataset.level = level;
    aqiExplanation.textContent = explanation;
    pm25Value.textContent = displayValue(pm25);
    pm10Value.textContent = displayValue(pm10);
    updatedAt.textContent = time ? formatEstimateTime(time) : "Unavailable";

    for (const band of aqiScaleBands.children) {
        band.classList.toggle("is-current", band.dataset.level === level);
    }
    aqiScaleMarker.hidden = !available;
    aqiScaleMarker.classList.toggle("is-overflow", overflow);
    aqiScaleMarker.style.insetInlineStart = available ? `${Math.min(aqi, AQI_SCALE_MAX) / AQI_SCALE_MAX * 100}%` : "";
    aqiScaleNote.textContent = overflow ? "Beyond the standard AQI scale" : "Estimate unavailable";
    aqiScaleNote.hidden = available && !overflow;
    const position = available ? `US AQI ${aqi}, ${category}. ${overflow ? "Beyond" : "On"} the 0 to 500 scale.` :
        "US AQI scale from 0 to 500. Estimate unavailable.";
    aqiScale.setAttribute("aria-label", position);
}

function displayValue(value) {
    return isValidReading(value) ? String(value) : "N/A";
}

function formatEstimateTime(value) {
    const time = parsePakistanTime(value);
    return Number.isFinite(time) ? `${pakistanDateTime.format(time)} PKT` : "Unavailable";
}

const forecast = new ForecastView(aqiLevels, getAQILevel);
let selectedCity = null;
let cityRequest = null;
let hasEstimate = false;

function selectCity(city) {
    citySelect.value = city;
    if (city !== selectedCity || !hasEstimate) loadAirQuality();
}

async function loadAirQuality() {
    const city = citySelect.value;
    if (city === selectedCity && cityRequest) return;

    cityRequest?.abort();
    const request = new AbortController();
    cityRequest = request;
    if (city !== selectedCity) {
        selectedCity = city;
        hasEstimate = false;
        cityHeading.textContent = `Air quality in ${city}`;
        renderCurrentEstimate();
        syncCitySelection();
    }

    statusText.dataset.state = "loading";
    statusText.textContent = `Loading data for ${city}…`;
    refreshButton.setAttribute("aria-disabled", "true");
    currentOverview.setAttribute("aria-busy", "true");
    forecast.clear(`Loading forecast for ${city}…`);

    try {
        const response = await fetch(
            `/api/air-quality?city=${encodeURIComponent(city)}`,
            { signal: request.signal }
        );

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const data = await response.json();
        if (request !== cityRequest) return;
        if (data?.city !== city) throw new Error("Unexpected city response");
        const current = data.current;
        forecast.load(data.hourly);

        if (!Number.isFinite(parsePakistanTime(current?.time)) ||
            ![current.us_aqi, current.pm2_5, current.pm10].some(isValidReading)) {
            statusText.dataset.state = "error";
            statusText.textContent = hasEstimate
                ? `Current estimate unavailable. Showing the last available estimate for ${city}. Try refreshing the data.`
                : `Current estimate unavailable for ${city}. Try refreshing the data.`;
            return;
        }

        renderCurrentEstimate(current);
        hasEstimate = true;
        statusText.dataset.state = "ready";
        statusText.textContent = `Showing latest available estimates for ${city}`;

    } catch (error) {
        if (request !== cityRequest) return;
        forecast.clear(`Forecast unavailable for ${city}. Try refreshing the data.`);
        statusText.dataset.state = "error";
        statusText.textContent = hasEstimate
            ? `Refresh failed. Showing the last available estimate for ${city}.`
            : `Unable to load estimates for ${city}. Try refreshing the data.`;
        console.error("Air quality request failed:", error);
    } finally {
        if (request === cityRequest) {
            cityRequest = null;
            refreshButton.removeAttribute("aria-disabled");
            currentOverview.setAttribute("aria-busy", "false");
        }
    }
}

let comparisonLoading = false;

function syncCitySelection() {
    for (const [city, { row, button, label }] of comparisonChoices) {
        const current = city === selectedCity;
        row.classList.toggle("is-selected", current);
        button.setAttribute("aria-current", String(current));
        label.hidden = !current;
    }
    for (const [city, marker] of mapMarkers) {
        const current = city === selectedCity;
        marker.getElement().setAttribute("aria-current", String(current));
        marker.setZIndexOffset(current ? 500 : 0);
        if (!current && marker.isPopupOpen()) marker.closePopup();
    }
}

function renderComparison() {
    const focusedCity = document.activeElement?.closest("#comparison-body .city-choice")?.dataset.city;
    const cities = [...supportedCities].sort((a, b) => {
        const left = comparisonReadings.get(a)?.aqi ?? -Infinity;
        const right = comparisonReadings.get(b)?.aqi ?? -Infinity;
        return left === right ? a.localeCompare(b, "en") : right - left;
    });

    const rows = document.createDocumentFragment();
    comparisonChoices.clear();
    for (const city of cities) {
        const { aqi, pm25, pm10 } = comparisonReadings.get(city) ?? {};
        const { category, level } = getAQILevel(aqi);
        const row = document.createElement("tr");
        row.setAttribute("role", "row");

        const heading = document.createElement("th");
        heading.scope = "row";
        heading.setAttribute("role", "rowheader");
        const button = document.createElement("button");
        button.type = "button";
        button.className = "city-choice";
        button.dataset.city = city;
        button.addEventListener("click", () => selectCity(city));
        const name = document.createElement("span");
        name.className = "city-choice-name";
        name.textContent = city;
        const label = document.createElement("span");
        label.className = "current-city-label";
        label.textContent = "Selected";
        label.setAttribute("aria-hidden", "true");
        button.append(name, label);
        heading.append(button);
        row.append(heading);

        for (const value of [displayValue(aqi), category, displayValue(pm25), displayValue(pm10)]) {
            const cell = row.insertCell();
            cell.setAttribute("role", "cell");
            cell.textContent = value;
        }
        row.cells[2].className = "category-marker";
        row.cells[2].dataset.level = level;

        comparisonChoices.set(city, { row, button, label });
        rows.append(row);
    }

    comparisonBody.replaceChildren(rows);
    comparisonScroll.hidden = false;
    syncCitySelection();
    comparisonChoices.get(focusedCity)?.button.focus({ preventScroll: true });
}

async function loadCityComparison() {
    if (comparisonLoading) return;

    comparisonLoading = true;
    comparisonStatus.textContent = "Loading city comparisons…";

    try {
        const response = await fetch("/api/compare");

        if (!response.ok) {
            throw new Error(`Server returned ${response.status}`);
        }

        const data = await response.json();
        if (!Array.isArray(data?.cities)) {
            throw new Error("City comparison data is unavailable");
        }

        const readings = data.cities.filter(record => supportedCities.includes(record?.city));
        if (!readings.length) throw new Error("No supported cities in comparison data");
        comparisonReadings = new Map(readings.map(record => [
            record.city, { ...record, aqi: isValidReading(record.aqi) ? record.aqi : null }
        ]));
        comparisonStatus.textContent = "";
    } catch (error) {
        comparisonReadings.clear();
        comparisonStatus.textContent = "Comparison estimates could not load. You can still select any city or try Refresh data.";
        console.error("City comparison request failed:", error);
    } finally {
        comparisonLoading = false;
    }

    renderComparison();
    updateMapMarkers();
}

function updateMapMarkers() {
    for (const [city, marker] of mapMarkers) {
        const { aqi, time } = comparisonReadings.get(city) ?? {};
        const { category, level } = getAQILevel(aqi);
        const pin = marker.getElement();
        pin.dataset.level = level;
        pin.setAttribute("aria-label", aqi == null ? `${city}, US AQI unavailable` : `${city}, US AQI ${aqi}, ${category}`);

        const popup = document.createElement("div");
        popup.className = "city-popup";
        const name = document.createElement("h3");
        name.textContent = city;
        const reading = document.createElement("div");
        reading.className = "popup-reading";
        const value = document.createElement("strong");
        value.textContent = displayValue(aqi);
        reading.append(value, " US AQI");
        const description = document.createElement("p");
        description.className = "category-marker";
        description.dataset.level = level;
        description.textContent = category;
        popup.append(name, reading, description);

        const validAt = parsePakistanTime(time);
        if (Number.isFinite(validAt)) {
            const validity = document.createElement("p");
            validity.className = "popup-validity";
            const timestamp = document.createElement("time");
            timestamp.dateTime = new Date(validAt).toISOString();
            timestamp.textContent = formatEstimateTime(time);
            validity.append("Estimate valid at ", timestamp);
            popup.append(validity);
        }
        marker.setPopupContent(popup);
    }
}

function initializeMapLegend() {
    const legend = document.getElementById("map-legend");
    for (const { level, category } of [...aqiLevels, unavailableAQI]) {
        const item = document.createElement("li");
        item.className = "category-marker";
        item.dataset.level = level;
        item.textContent = category;
        legend.append(item);
    }
    const selected = document.createElement("li");
    selected.className = "selection-key";
    selected.textContent = "Selected city";
    legend.append(selected);
}

function hasLeafletPaneLayout() {
    // Tile and marker placement requires Leaflet's pane positioning rules.
    const pane = document.createElement("div");
    pane.className = "leaflet-pane";
    mapFrame.append(pane);
    const positioned = getComputedStyle(pane).position === "absolute";
    pane.remove();
    return positioned;
}

async function initializeMap() {
    if (typeof L === "undefined" || !hasLeafletPaneLayout()) {
        mapStatus.textContent = "Map unavailable. Choose a city from the comparison or the city menu.";
        mapFrame.hidden = true;
        return;
    }

    let markerState = "loading";
    let tileState = "loading";
    let loadedTiles = 0;
    let failedTiles = 0;

    function reportMapStatus() {
        const markerMessage = {
            loading: "Loading city markers…",
            ready: "",
            error: "City markers could not load. Choose a city from the comparison or city menu."
        }[markerState];
        const tileMessage = {
            loading: "Loading map tiles…",
            ready: "",
            partial: "Some map tiles could not load.",
            error: "Map tiles could not load."
        }[tileState];
        mapStatus.textContent = [markerMessage, tileMessage].filter(Boolean).join(" ");
    }

    try {
        const reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
        const map = L.map(mapFrame, {
            scrollWheelZoom: false,
            fadeAnimation: false,
            zoomAnimation: !reducedMotion,
            markerZoomAnimation: !reducedMotion,
            inertia: !reducedMotion
        }).setView([30.4, 69.3], 5);
        if (reducedMotion) {
            // Leaflet popups call panBy when they need more room inside the map.
            const panBy = map.panBy.bind(map);
            map.panBy = (offset, options) => panBy(offset, { ...options, animate: false });
        }

        const tiles = L.tileLayer(
            "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
            {
                attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
                maxZoom: 18
            }
        );
        tiles.on({
            loading() {
                loadedTiles = failedTiles = 0;
                tileState = "loading";
                reportMapStatus();
            },
            tileload() { loadedTiles++; },
            tileerror() { failedTiles++; },
            load() {
                tileState = failedTiles ? (loadedTiles ? "partial" : "error") : "ready";
                reportMapStatus();
            }
        }).addTo(map);

        const response = await fetch("/api/cities");
        if (!response.ok) throw new Error("Failed to load cities");
        const cities = await response.json();
        if (!Array.isArray(cities) || !cities.length) throw new Error("City locations unavailable");

        const icon = L.divIcon({
            className: "city-map-pin", iconSize: [36, 36], iconAnchor: [18, 18], popupAnchor: [0, -14]
        });

        for (const city of cities) {
            const { name, latitude, longitude } = city ?? {};
            if (!supportedCities.includes(name) || mapMarkers.has(name)) continue;
            if (!Number.isFinite(latitude) || Math.abs(latitude) > 90 ||
                !Number.isFinite(longitude) || Math.abs(longitude) > 180) continue;
            const marker = L.marker([latitude, longitude], { icon }).addTo(map);
            marker.bindPopup(document.createElement("div"), { maxWidth: 218 });
            mapMarkers.set(name, marker);
            marker.on("click", () => selectCity(name));

            const pin = marker.getElement();
            pin.dataset.city = name;
            const restorePinFocus = event => {
                if (document.activeElement === event.currentTarget) pin.focus({ preventScroll: true });
            };
            const handlePopupKey = event => {
                if (event.key !== "Escape" && event.key !== " ") return;
                event.preventDefault();
                event.stopPropagation();
                restorePinFocus(event);
                marker.closePopup();
            };
            marker.on("popupopen", ({ popup }) => {
                const closeButton = popup.getElement().querySelector(".leaflet-popup-close-button");
                closeButton.addEventListener("click", restorePinFocus, true);
                closeButton.addEventListener("keydown", handlePopupKey);
            });
            pin.addEventListener("keydown", event => {
                if (event.key === "Escape" && marker.isPopupOpen()) {
                    event.stopPropagation();
                    marker.closePopup();
                } else if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    if (event.repeat) return;
                    selectCity(name);
                    marker.openPopup();
                }
            });
        }

        updateMapMarkers();
        syncCitySelection();
        mapKey.hidden = !mapMarkers.size;
        markerState = mapMarkers.size === supportedCities.length ? "ready" : "error";
    } catch (error) {
        console.error("Map initialization failed:", error);
        markerState = "error";
    }
    reportMapStatus();
}

citySelect.addEventListener("change", () => selectCity(citySelect.value));
refreshButton.addEventListener("click", () => {
    if (cityRequest) return;
    loadAirQuality();
    loadCityComparison();
});

initializeAQIReference();
initializeMapLegend();
renderComparison();
loadAirQuality();
loadCityComparison();
initializeMap();
