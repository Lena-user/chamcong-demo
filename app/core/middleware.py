"""Request id + one structured (JSON) access log line per request."""
import json
import logging
import re
import sys
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import get_settings

access_log = logging.getLogger("app.access")
_VALID_ID = re.compile(r"^[A-Za-z0-9._\-]{1,64}$")


def setup_logging() -> None:
    """JSON lines on stdout for the access log; plain text for the rest of the app's loggers."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    access_log.handlers = [handler]
    access_log.setLevel(logging.INFO)
    access_log.propagate = False

    app_logger = logging.getLogger("app")
    if not app_logger.handlers:
        h = logging.StreamHandler(sys.stderr)
        h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        app_logger.addHandler(h)
        app_logger.setLevel(logging.INFO)


class RequestContextMiddleware:
    """Accepts or generates X-Request-ID, returns it, and (ACCESS_LOG=true) logs the request."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        state = scope.setdefault("state", {})
        state["request_id"] = request_id
        status_code = 500
        started = time.perf_counter()

        async def send_wrapper(message: Message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if get_settings().access_log:
                route = getattr(scope.get("route"), "path", None) or ("static" if status_code < 400 else "unmatched")
                access_log.info(json.dumps({
                    "request_id": request_id,
                    "method": scope["method"],
                    "route": route,
                    "status": status_code,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                    "user_id": state.get("user_id"),
                }))
