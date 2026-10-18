import time

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response


class TokenBucketLimiter(BaseHTTPMiddleware):
    """Per-client token bucket. Buckets are in memory and pruned lazily; fine for a single instance."""

    def __init__(
        self,
        app,
        per_minute: int,
        burst: int,
        trust_proxy: bool,
        path_prefix: str = "/api/",
    ) -> None:
        super().__init__(app)
        self.rate = per_minute / 60.0
        self.burst = float(burst)
        self.trust_proxy = trust_proxy
        self.prefix = path_prefix
        self._buckets: dict[str, tuple[float, float]] = {}  # ip -> (tokens, last_refill)
        self._last_prune = time.monotonic()

    def client_ip(self, request: Request) -> str:
        if self.trust_proxy:
            fwd = request.headers.get("x-forwarded-for")
            if fwd:
                return fwd.split(",")[0].strip()
        return request.client.host if request.client else "unknown"

    def _take(self, ip: str) -> tuple[bool, float]:
        now = time.monotonic()
        tokens, last = self._buckets.get(ip, (self.burst, now))
        tokens = min(self.burst, tokens + (now - last) * self.rate)
        if tokens >= 1.0:
            self._buckets[ip] = (tokens - 1.0, now)
            return True, 0.0
        self._buckets[ip] = (tokens, now)
        retry_after = (1.0 - tokens) / self.rate
        if now - self._last_prune > 300:
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < 300}
            self._last_prune = now
        return False, retry_after

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if not request.url.path.startswith(self.prefix):
            return await call_next(request)
        allowed, retry_after = self._take(self.client_ip(request))
        if not allowed:
            return JSONResponse(
                {"detail": "Rate limit exceeded. Slow down and retry."},
                status_code=429,
                headers={"Retry-After": str(max(1, int(retry_after) + 1))},
            )
        return await call_next(request)
