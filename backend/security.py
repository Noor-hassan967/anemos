from time import monotonic

from starlette.datastructures import MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send


API_REQUESTS_PER_SECOND = 20
API_BURST = 60
RATE_LIMITED_PATHS = {"/api/air-quality", "/api/compare", "/api/cities"}
DOCS_PAGES = {"/docs", "/docs/oauth2-redirect", "/redoc"}
SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; "
        "script-src 'self' https://cdn.jsdelivr.net/npm/chart.js@4.4.8/dist/chart.umd.min.js "
        "https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js; "
        "style-src 'self' https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css; "
        "img-src 'self' data: https://tile.openstreetmap.org https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/images/; "
        "connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


class ApiRateLimiter:
    def __init__(self):
        self._tokens = API_BURST
        self._updated_at = monotonic()

    def allow(self) -> bool:
        now = monotonic()
        self._tokens = min(API_BURST, self._tokens + (now - self._updated_at) * API_REQUESTS_PER_SECOND)
        self._updated_at = now
        if self._tokens < 1:
            return False
        self._tokens -= 1
        return True


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, docs_enabled: bool):
        self.app = app
        self.docs_enabled = docs_enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope["path"].rstrip("/")

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers[name] = value
                if self.docs_enabled and path in DOCS_PAGES:
                    del headers["Content-Security-Policy"]
            await send(message)

        if path in RATE_LIMITED_PATHS and not scope["app"].state.api_rate_limiter.allow():
            response = JSONResponse(
                {"detail": "Too many API requests. Please try again shortly."},
                status_code=429,
                headers={"Retry-After": "1", "Cache-Control": "no-store"},
            )
            await response(scope, receive, send_with_headers)
            return

        await self.app(scope, receive, send_with_headers)
