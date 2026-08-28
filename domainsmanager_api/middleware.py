import json
import logging
import re
from threading import Lock
from time import perf_counter
from uuid import uuid4

from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,128}$")
logger = logging.getLogger("domainsmanager.access")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp, header_name: str = "X-Request-ID") -> None:
        self.app = app
        self.header_name = header_name
        self.header_bytes = header_name.lower().encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        supplied = headers.get(self.header_bytes, b"").decode(
            "ascii", errors="ignore"
        )
        request_id = supplied if REQUEST_ID_PATTERN.fullmatch(supplied) else uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id
        started_at = perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                response_headers = list(message.get("headers", []))
                response_headers.append((self.header_bytes, request_id.encode("ascii")))
                message["headers"] = response_headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            logger.info(
                json.dumps(
                    {
                        "event": "http_request",
                        "request_id": request_id,
                        "method": scope["method"],
                        "path": scope["path"],
                        "status_code": status_code,
                        "duration_ms": round((perf_counter() - started_at) * 1000, 3),
                    },
                    separators=(",", ":"),
                )
            )


class AuthRateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        paths: set[str],
        attempts: int,
        window_seconds: int,
    ) -> None:
        self.app = app
        self.paths = paths
        self.attempts = attempts
        self.window_seconds = window_seconds
        self._windows: dict[tuple[str, str], tuple[float, int]] = {}
        self._last_cleanup = perf_counter()
        self._lock = Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope["method"] != "POST"
            or scope["path"] not in self.paths
        ):
            await self.app(scope, receive, send)
            return

        now = perf_counter()
        client = scope.get("client")
        peer = client[0] if client else "unknown"
        key = (peer, scope["path"])
        with self._lock:
            if now - self._last_cleanup >= self.window_seconds:
                self._windows = {
                    item_key: item
                    for item_key, item in self._windows.items()
                    if now - item[0] < self.window_seconds
                }
                self._last_cleanup = now
            started_at, count = self._windows.get(key, (now, 0))
            if now - started_at >= self.window_seconds:
                started_at, count = now, 0
            limited = count >= self.attempts
            if not limited:
                self._windows[key] = (started_at, count + 1)
            retry_after = max(1, int(self.window_seconds - (now - started_at)))

        if not limited:
            await self.app(scope, receive, send)
            return

        request_id = scope.get("state", {}).get("request_id", "unknown")
        body = json.dumps(
            {
                "code": "rate_limited",
                "message": "Too many authentication requests",
                "request_id": request_id,
            },
            separators=(",", ":"),
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                    (b"retry-after", str(retry_after).encode("ascii")),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
