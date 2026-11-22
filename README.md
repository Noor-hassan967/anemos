# PakAir

PakAir shows modeled air-quality readings, forecasts, and city comparisons for Pakistan. FastAPI serves the dashboard and its same-origin JSON API.

## Run locally

From the project root, with the dependencies in `requirements.txt` already installed in `.venv`:

```sh
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --no-proxy-headers
.venv/bin/python -m pytest -q
```

Open `http://127.0.0.1:8000/`. The tests use mocked Open-Meteo responses. Swagger UI, ReDoc, and the OpenAPI schema are disabled by default. For local API documentation, set `PAKAIR_ENABLE_API_DOCS=1` when starting the loopback server. The interactive docs load their own assets and are exempt from the dashboard's Content Security Policy. FastAPI debug mode stays off.

## Security review

- The API accepts only supported city names. It uses server-owned coordinates and a fixed Open-Meteo HTTPS endpoint with redirects disabled and bounded timeouts. It validates provider responses and returns generic error messages to clients.
- The public file root is the repository's `frontend/` directory. Files outside it and symlinks pointing outside it are excluded. Treat every file placed in `frontend/` as public. A pattern-based check of tracked files and Git history found no likely credentials. Environment files, AWS configuration, and common private-key files are excluded from Git by default.
- The dashboard uses a Content Security Policy, framing protection, MIME sniffing protection, and restrictive browser permissions. The frontend calls the same origin, so the API sends no cross-origin permissions. CORS is a browser control and public endpoints can still be called directly.
- Chart.js and Leaflet use pinned CDN URLs and integrity hashes. Update the URL, hash, and Content Security Policy together when changing either version. Leaflet's popup API can render raw HTML strings. PakAir passes DOM nodes and inserts city and AQI values as text. [Leaflet popup API](https://leafletjs.com/reference.html#layer-bindpopup)
- Map tiles come from `https://tile.openstreetmap.org`. The map links to OpenStreetMap attribution, and `strict-origin-when-cross-origin` sends the site's origin as the referrer on cross-origin HTTPS tile requests. Browser caching follows tile response headers. Tile delivery depends on OpenStreetMap availability and its usage policy. [OpenStreetMap tile policy](https://operations.osmfoundation.org/policies/tiles/)
- Each worker allows an aggregate burst of 60 requests and replenishes 20 requests per second across `/api/air-quality`, `/api/compare`, and `/api/cities`, including cache hits. Excess requests get `429` with `Retry-After: 1`. Static assets and `/api/health` remain available. The separate 10-minute cache, three-call Open-Meteo concurrency cap, and one-second wait for outbound capacity also apply per worker.

### Dependency audit

On November 22, 2026, the PyPI release advisory API reported no active advisories for the 28 exact versions in `requirements.txt`. The local installer, `pip 25.1.1`, had six distinct CVEs. The reported fixes span `25.3` through `26.2`. Use an approved, patched installer for builds and rerun the audit on the environment you plan to deploy. Pip is a build tool here. The audit covers published advisories for the application pins and local pip version. Check operating-system and Python interpreter security separately. [PyPI advisory API](https://docs.pypi.org/api/json/#known-vulnerabilities) · [Advisories for pip 25.1.1](https://pypi.org/pypi/pip/25.1.1/json)

The pip findings were `CVE-2025-8869`, `CVE-2026-1703`, `CVE-2026-3219`, `CVE-2026-6357`, `CVE-2026-8643`, and `CVE-2026-13346`.

## Deploy behind HTTPS

For a TLS-terminating reverse proxy on the same machine, start the app on a private loopback port:

```sh
.venv/bin/python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000 \
  --proxy-headers --forwarded-allow-ips=127.0.0.1 --no-server-header
```

- **TLS and host validation.** Configure certificates, HTTP-to-HTTPS redirects, expected hostnames, and HSTS at the gateway. Keep the Uvicorn origin private. The gateway should replace incoming forwarded headers with trusted values. If the proxy is on another host, use a private origin and trust only the actual proxy address or network. Keep API docs opt-in disabled and leave reload off. [FastAPI proxy configuration](https://fastapi.tiangolo.com/advanced/behind-a-proxy/)
- **Traffic limits.** Apply per-client and aggregate limits at the gateway, with connection and request-size limits. A single client can consume the application's shared bucket. Workers and instances each start with a cold cache and their own limits, so scaling out increases total possible provider traffic. Use the gateway for shared enforcement across workers.
- **Provider dependencies.** Confirm Open-Meteo quota and usage terms for the deployment. Monitor `429`, `502`, and `503` rates and provider availability. The CDN and tile server remain external dependencies, and browsers disclose their IP addresses and site origin during those requests. [Open-Meteo plan and usage information](https://open-meteo.com/en/pricing)
- **Hosting verification.** Check the deployed hostname, certificate, HTTPS redirect, HSTS, forwarded-header handling, and gateway limits in staging. Keep deployment credentials in the hosting provider's secret store.
