const HOUR_MS = 60 * 60 * 1000;
const DAY_MS = 24 * HOUR_MS;
const PKT_OFFSET_MS = 5 * HOUR_MS;

const pakistanDateTime = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Karachi", dateStyle: "medium", timeStyle: "short"
});
const pakistanDay = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Karachi", day: "numeric", month: "short"
});
const pakistanHour = new Intl.DateTimeFormat("en-GB", {
    timeZone: "Asia/Karachi", hour: "2-digit", minute: "2-digit", hourCycle: "h23"
});

function parsePakistanTime(value) {
    if (typeof value !== "string") return NaN;
    const iso = /^(\d{4}-\d{2}-\d{2})T(?:[01]\d|2[0-3]):[0-5]\d(?::[0-5]\d(?:\.\d{1,3})?)?(Z|[+-]\d{2}:?\d{2})?$/i.exec(value);
    if (!iso) return NaN;

    const day = new Date(iso[1]);
    if (!Number.isFinite(day.getTime()) || day.toISOString().slice(0, 10) !== iso[1]) return NaN;
    return Date.parse(iso[2] ? value : `${value}+05:00`);
}

function isValidReading(value) {
    return Number.isFinite(value) && value >= 0;
}

const forecastMetrics = {
    us_aqi: { name: "US AQI", unit: "", stableDifference: 5 },
    pm2_5: { name: "PM2.5", unit: "µg/m³", stableDifference: 1 },
    pm10: { name: "PM10", unit: "µg/m³", stableDifference: 1 }
};

function upcomingRows(hourly, field, now) {
    const times = hourly?.time;
    if (!Array.isArray(times)) return [];
    const values = Array.isArray(hourly[field]) ? hourly[field] : [];
    const rows = new Map();

    times.forEach((time, index) => {
        const x = parsePakistanTime(time);
        if (!Number.isFinite(x) || x % HOUR_MS !== 0 || x < now || x >= now + DAY_MS) return;
        const value = values[index];
        const y = isValidReading(value) ? value : null;
        if (!rows.has(x) || rows.get(x).y === null) rows.set(x, { x, y });
    });

    return [...rows.values()].sort((a, b) => a.x - b.x);
}

function chartTimeTicks(start, end, midnight, width) {
    if (start === end) return [start];

    // Prefer the day transition, then use endpoints and evenly spaced local hours when they fit.
    const candidates = midnight === null ? [start, end] : [midnight, start, end];
    const step = (width < 480 ? 6 : 4) * HOUR_MS;
    for (let time = Math.ceil((start + PKT_OFFSET_MS) / step) * step - PKT_OFFSET_MS; time < end; time += step) {
        candidates.push(time);
    }

    const spacing = (end - start) * 63 / Math.max(width - 72, 140);
    const ticks = [];
    for (const time of candidates) {
        if (ticks.every(tick => Math.abs(tick - time) >= spacing)) ticks.push(time);
    }
    return ticks.sort((a, b) => a - b);
}

class ForecastView {
    constructor(aqiLevels, categoryForAQI) {
        this.aqiLevels = aqiLevels;
        this.categoryForAQI = categoryForAQI;
        const controls = document.querySelector(".forecast-metrics");
        this.field = controls.querySelector("input:checked").value;
        this.hourly = null;
        this.message = "Waiting for forecast data…";
        this.chart = null;
        this.timer = null;

        this.coverage = document.getElementById("forecast-coverage");
        this.summary = document.getElementById("forecast-summary");
        this.plot = document.getElementById("forecast-plot");
        this.canvas = document.getElementById("aqi-chart");
        this.visualNote = document.getElementById("forecast-visual-note");
        this.guides = document.getElementById("forecast-guides");
        this.guideLabels = document.getElementById("forecast-guide-labels");
        this.details = document.getElementById("forecast-details");
        this.caption = document.getElementById("forecast-table-caption");
        this.valueHeading = document.getElementById("forecast-value-heading");
        this.categoryHeading = document.getElementById("forecast-category-heading");
        this.tableBody = document.getElementById("forecast-table-body");

        controls.addEventListener("change", event => {
            this.field = event.target.value;
            this.render();
        });
        document.addEventListener("visibilitychange", () => {
            if (!document.hidden && this.hourly) this.render();
        });
    }

    clear(message) {
        this.hourly = null;
        this.message = message;
        this.render();
    }

    load(hourly) {
        this.hourly = hourly;
        this.message = "No upcoming forecast is available.";
        this.render();
    }

    removeChart(chart = this.chart) {
        try {
            chart?.destroy();
        } catch (error) {
            console.error("Forecast chart could not be cleared:", error);
        }
        this.chart = null;
    }

    render() {
        clearTimeout(this.timer);
        this.removeChart();
        this.plot.hidden = true;
        this.visualNote.hidden = true;
        this.guides.hidden = true;
        this.summary.hidden = true;
        this.summary.textContent = "";

        const metric = forecastMetrics[this.field];
        const isAQI = this.field === "us_aqi";
        const now = Date.now();
        const rows = upcomingRows(this.hourly, this.field, now);
        const plotted = rows.filter(row => row.y !== null);

        this.renderTable(rows, metric, isAQI);
        if (this.hourly) {
            this.timer = setTimeout(() => this.render(), HOUR_MS - now % HOUR_MS + 100);
        }

        if (!plotted.length) {
            this.coverage.textContent = rows.length ? `No upcoming ${metric.name} readings are available.` : this.message;
            return;
        }

        const first = plotted[0];
        const last = plotted.at(-1);
        const firstTime = pakistanDateTime.format(first.x);
        const range = first.x === last.x ? firstTime : `${firstTime} to ${pakistanDateTime.format(last.x)}`;
        this.coverage.textContent = `${range} PKT · ${plotted.length} of 24 hourly values available`;

        if (plotted.length >= 3 && last.x - first.x >= 2 * HOUR_MS) {
            const change = last.y - first.y;
            const trend = Math.abs(change) <= metric.stableDifference ? "stay roughly steady" :
                change > 0 ? "rise" : "fall";
            const unit = metric.unit ? ` ${metric.unit}` : "";
            this.summary.textContent = `The model forecasts ${metric.name} to ${trend} over this window, from ${first.y}${unit} to ${last.y}${unit}.`;
        } else {
            this.summary.textContent = "Too few hourly values to describe a forecast trend.";
        }
        this.summary.hidden = false;

        // The table and summary already exist when the optional canvas library is unavailable.
        if (typeof Chart !== "function") {
            this.visualNote.hidden = false;
            return;
        }

        try {
            this.renderChart(rows, plotted, metric, isAQI);
        } catch (error) {
            this.plot.hidden = true;
            this.visualNote.hidden = false;
            this.removeChart(Chart.getChart?.(this.canvas));
            console.error("Forecast chart could not be rendered:", error);
        }
    }

    renderTable(rows, metric, isAQI) {
        const output = document.createDocumentFragment();
        for (const { x, y } of rows) {
            const row = document.createElement("tr");
            const timeCell = document.createElement("th");
            timeCell.scope = "row";
            const time = document.createElement("time");
            time.dateTime = new Date(x).toISOString();
            time.textContent = pakistanDateTime.format(x);
            timeCell.append(time);
            row.append(timeCell);
            row.insertCell().textContent = y === null ? "Unavailable" : String(y);
            if (isAQI) row.insertCell().textContent = this.categoryForAQI(y).category;
            output.append(row);
        }

        this.caption.textContent = `Hourly ${metric.name} model estimates`;
        this.valueHeading.textContent = metric.unit ? `${metric.name} (${metric.unit})` : metric.name;
        this.categoryHeading.hidden = !isAQI;
        this.tableBody.replaceChildren(output);
        this.details.hidden = !rows.length;
    }

    renderChart(rows, plotted, metric, isAQI) {
        const start = rows[0].x;
        const end = rows.at(-1).x;
        const nextMidnight = (Math.floor((start + PKT_OFFSET_MS) / DAY_MS) + 1) * DAY_MS - PKT_OFFSET_MS;
        const midnight = nextMidnight <= end ? nextMidnight : null;
        const peak = Math.max(...plotted.map(row => row.y));
        const upper = isAQI ? Math.max(10, Math.ceil(peak * 1.12 / 10) * 10) : undefined;
        const guides = isAQI ? this.aqiLevels.filter(level => level.max < upper) : [];
        const boundaries = new Set(guides.map(level => level.max));
        const points = rows.flatMap((row, index) => {
            const previous = rows[index - 1];
            return previous && row.x - previous.x > HOUR_MS ? [{ x: previous.x + HOUR_MS, y: null }, row] : [row];
        });

        const styles = getComputedStyle(this.canvas);
        const ink = styles.getPropertyValue("--chart-ink").trim();
        const line = styles.getPropertyValue("--chart-line").trim();
        const grid = styles.getPropertyValue("--chart-grid").trim();
        const reference = styles.getPropertyValue("--chart-reference").trim();
        this.canvas.setAttribute("aria-label", `${metric.name} forecast`);
        this.plot.hidden = false;

        const referenceLines = {
            id: "forecastReferenceLines",
            beforeDatasetsDraw({ ctx, chartArea, scales }) {
                const { left, right, top, bottom } = chartArea;
                ctx.save();
                ctx.strokeStyle = reference;
                ctx.lineWidth = 1;
                ctx.setLineDash([4, 5]);
                for (const guide of boundaries) {
                    const y = scales.y.getPixelForValue(guide);
                    ctx.beginPath();
                    ctx.moveTo(left, y);
                    ctx.lineTo(right, y);
                    ctx.stroke();
                }
                if (midnight !== null) {
                    const x = scales.x.getPixelForValue(midnight);
                    ctx.beginPath();
                    ctx.moveTo(x, top);
                    ctx.lineTo(x, bottom);
                    ctx.stroke();
                }
                ctx.restore();
            }
        };

        this.chart = new Chart(this.canvas, {
            type: "line",
            data: { datasets: [{
                label: metric.name, data: points, borderColor: line, backgroundColor: line,
                borderWidth: 2.5, fill: false, tension: 0, spanGaps: false, clip: 5,
                pointRadius: plotted.length === 1 ? 4 : 2, pointHoverRadius: 5
            }] },
            plugins: [referenceLines],
            options: {
                responsive: true, maintainAspectRatio: false, animation: false, parsing: false,
                interaction: { mode: "nearest", axis: "x", intersect: false },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        displayColors: false,
                        backgroundColor: styles.getPropertyValue("--surface").trim(),
                        borderColor: reference, borderWidth: 1, padding: 10,
                        callbacks: {
                            title: items => `${pakistanDateTime.format(items[0].parsed.x)} PKT`,
                            label: item => `${metric.name}: ${item.parsed.y}${metric.unit ? " " + metric.unit : ""}`,
                            afterLabel: item => isAQI ? this.categoryForAQI(item.parsed.y).category : ""
                        }
                    }
                },
                scales: {
                    x: {
                        type: "linear",
                        min: start === end ? start - HOUR_MS / 2 : start,
                        max: start === end ? end + HOUR_MS / 2 : end,
                        grid: { display: false }, border: { color: reference },
                        afterBuildTicks: scale => {
                            scale.ticks = chartTimeTicks(start, end, midnight, scale.chart.width)
                                .map(value => ({ value }));
                        },
                        ticks: {
                            color: ink, autoSkip: false, minRotation: 0, maxRotation: 0, align: "inner", padding: 6,
                            callback: (time, index, ticks) => {
                                const day = pakistanDay.format(time);
                                const hour = pakistanHour.format(time);
                                return index === 0 || day !== pakistanDay.format(ticks[index - 1].value) ? [day, hour] : hour;
                            }
                        }
                    },
                    y: {
                        min: 0, max: upper, suggestedMax: peak === 0 && !isAQI ? 5 : undefined,
                        title: { display: true, color: ink, text: metric.unit ? `${metric.name} (${metric.unit})` : metric.name },
                        afterBuildTicks: scale => {
                            if (!boundaries.size) return;
                            const gap = scale.max * 22 / Math.max(scale.chart.height - 64, 120);
                            const ticks = [0];
                            for (const value of [...boundaries, scale.max]) {
                                if (value - ticks.at(-1) >= gap) ticks.push(value);
                            }
                            scale.ticks = ticks.map(value => ({ value }));
                        },
                        ticks: { color: ink, maxTicksLimit: 9, autoSkip: false },
                        grid: { drawTicks: false, color: context => boundaries.has(context.tick?.value) ? "transparent" : grid },
                        border: { display: false }
                    }
                }
            }
        });
        if (!this.chart.ctx) throw new Error("Canvas context unavailable");

        this.guideLabels.replaceChildren(...guides.map(({ max, category }) => {
            const item = document.createElement("li");
            item.textContent = `${max} ${category}`;
            return item;
        }));
        this.guides.hidden = !guides.length;
    }
}
