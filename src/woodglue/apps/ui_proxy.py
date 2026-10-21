"""
Reverse proxy for Process-backed UIs at `/ui/<name>/`, for HTTP and websockets.

Requests keep their full path (`/ui/<name>/...`) and `Host` header, so the UI
process must serve under its base path, as `woodglue.apps.ui_process` sets up
for Streamlit. Before forwarding, the proxy:

- checks `view` on `ui:<name>` like any UI (see `woodglue.apps.uis`);
- removes the woodglue Session cookie, keeping the app's own cookies such as
  Streamlit's `_streamlit_xsrf`, so the process never holds a credential
  that outlives the Session (see `docs/adr/0003`);
- removes `Authorization` and any client-sent `X-Woodglue-*` header;
- adds `X-Woodglue-User` (JSON with `id`, `name`, `email`, `roles`) and, for
  a browser Session, `X-Woodglue-Token`, a Delegated Token the process uses to
  call the API as that User, limited to the UI's `grants` (see
  `woodglue.ui_support`).

Response bodies pass through as they arrive, so event streams and long polls
work; request bodies are read in full first.

Websockets opened under a Session are closed when it ends: at once on logout,
and within `SWEEP_SECONDS` when it expires or its User is disabled.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable
from typing import Any

import tornado.httpclient
import tornado.httputil
import tornado.ioloop
import tornado.web
import tornado.websocket
from typing_extensions import override

from woodglue.apps.authn import SESSION_COOKIE, Authentication, authenticate, public_scheme
from woodglue.apps.ui_process import UiProcesses
from woodglue.apps.uis import identity_headers, require_view
from woodglue.auth_store import AuthStore

logger = logging.getLogger(__name__)

SWEEP_SECONDS = 30

_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",  # codespell:ignore
    "trailer",
    "transfer-encoding",
    "upgrade",
}
_NOT_FORWARDED = _HOP_BY_HOP | {"authorization", "content-length", "cookie"}


def strip_cookie(header: str, name: str) -> str:
    """
    Remove one cookie from a `Cookie` header, keeping the rest as sent.

    >>> strip_cookie("a=1; wgl_session=s; b=2", "wgl_session")
    'a=1; b=2'
    >>> strip_cookie("wgl_session=s", "wgl_session")
    ''
    """
    kept = [p.strip() for p in header.split(";") if p.strip().split("=", 1)[0] != name]
    return "; ".join(p for p in kept if p)


class _ResponseRelay:
    """
    Passes an upstream response to the client as it arrives: the status and
    headers once they are complete, then each body chunk, flushed at once.
    """

    handler: tornado.web.RequestHandler
    method: str
    started: bool
    _status_line: str | None
    _headers: tornado.httputil.HTTPHeaders

    def __init__(self, handler: tornado.web.RequestHandler, method: str) -> None:
        self.handler = handler
        self.method = method
        self.started = False
        self._status_line = None
        self._headers = tornado.httputil.HTTPHeaders()

    def on_header_line(self, line: str) -> None:
        if self._status_line is None:
            self._status_line = line
            return
        if line.strip():
            self._headers.parse_line(line)
            return
        start = tornado.httputil.parse_response_start_line(self._status_line.strip())
        if start.code == 100:
            self._status_line = None
            self._headers = tornado.httputil.HTTPHeaders()
            return
        self._start(start.code, start.reason)

    def _start(self, code: int, reason: str) -> None:
        handler = self.handler
        handler.set_status(code, reason)
        handler.clear_header("Content-Type")
        handler.clear_header("Server")
        for name, value in self._headers.get_all():
            lower = name.lower()
            if lower in _HOP_BY_HOP:
                continue
            if lower == "content-length" and (self.method == "HEAD" or code in (204, 304)):
                continue
            handler.add_header(name, value)
        self.started = True

    def on_chunk(self, chunk: bytes) -> None:
        if chunk and self.method != "HEAD":
            self.handler.write(chunk)
            self.handler.flush()


class UiSockets:
    """Open proxied websockets by Session, so they can be closed when it ends."""

    _by_session: dict[str, set[UiProxyHandler]]
    _sweeper: tornado.ioloop.PeriodicCallback | None

    def __init__(self) -> None:
        self._by_session = {}
        self._sweeper = None

    def add(self, session_secret: str, handler: UiProxyHandler) -> None:
        self._by_session.setdefault(session_secret, set()).add(handler)
        if self._sweeper is None:
            self._sweeper = tornado.ioloop.PeriodicCallback(self._sweep, SWEEP_SECONDS * 1000)
            self._sweeper.start()

    def remove(self, session_secret: str, handler: UiProxyHandler) -> None:
        handlers = self._by_session.get(session_secret)
        if handlers is not None:
            handlers.discard(handler)
            if not handlers:
                del self._by_session[session_secret]

    def close_session(self, session_secret: str) -> None:
        for handler in list(self._by_session.pop(session_secret, ())):
            handler.close(1008, "Session ended")

    def _sweep(self) -> None:
        for secret, handlers in list(self._by_session.items()):
            store: AuthStore | None = next(iter(handlers)).application.settings.get("auth_store")
            if store is not None and not store.session_is_live(secret):
                self.close_session(secret)


class UiProxyHandler(tornado.websocket.WebSocketHandler):
    """Proxies one Process-backed UI; a websocket upgrade switches to frame relaying."""

    SUPPORTED_METHODS: tuple[str, ...] = (
        "GET",
        "HEAD",
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
        "OPTIONS",
    )  # pyright: ignore[reportIncompatibleVariableOverride]

    ui_name: str = ""
    public: bool = False
    auth: Authentication | None = None
    _upstream_ws: tornado.websocket.WebSocketClientConnection | None = None
    _client_subprotocols: list[str] | None = None
    _relay: asyncio.Task[None] | None = None

    @override
    def initialize(self, ui_name: str, public: bool = False) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]
        self.ui_name = ui_name
        self.public = public

    @override
    def prepare(self) -> None:
        self.auth = authenticate(self) if self.public else require_view(self, self.ui_name)

    def _upstream_base(self) -> str | None:
        processes: UiProcesses = self.application.settings["ui_processes"]
        return processes.upstream(self.ui_name)

    def _upstream_headers(self) -> tornado.httputil.HTTPHeaders:
        headers = tornado.httputil.HTTPHeaders()
        for name, value in self.request.headers.get_all():
            lower = name.lower()
            if lower in _NOT_FORWARDED or lower.startswith(("x-woodglue-", "sec-websocket-")):
                continue
            headers.add(name, value)
        cookie = strip_cookie(self.request.headers.get("Cookie", ""), SESSION_COOKIE)
        if cookie:
            headers["Cookie"] = cookie
        headers["X-Forwarded-Proto"] = public_scheme(self)
        headers["X-Forwarded-Host"] = self.request.host
        if self.request.remote_ip:
            headers["X-Forwarded-For"] = self.request.remote_ip
        if self.auth is not None:
            store: AuthStore | None = self.application.settings.get("auth_store")
            entry = self.application.settings["config"].uis.get(self.ui_name)
            grants = entry.grants if entry is not None else None
            for name, value in identity_headers(self.auth, store, self.ui_name, grants).items():
                headers[name] = value
        return headers

    # HTTP

    @override
    async def get(self, *args: Any, **kwargs: Any) -> None:
        if self.request.headers.get("Upgrade", "").lower() == "websocket":
            await super().get(*args, **kwargs)
        else:
            await self._proxy_http()

    @override
    def head(self, *args: Any) -> Awaitable[None]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._proxy_http()

    @override
    def post(self, *args: Any) -> Awaitable[None]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._proxy_http()

    @override
    def put(self, *args: Any) -> Awaitable[None]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._proxy_http()

    @override
    def patch(self, *args: Any) -> Awaitable[None]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._proxy_http()

    @override
    def delete(self, *args: Any) -> Awaitable[None]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._proxy_http()

    @override
    def options(self, *args: Any) -> Awaitable[None]:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self._proxy_http()

    async def _proxy_http(self) -> None:
        base = self._upstream_base()
        if base is None:
            self.set_status(503)
            self.finish(f"UI '{self.ui_name}' is starting or unavailable; try again shortly")
            return
        method = self.request.method or "GET"
        relay = _ResponseRelay(self, method)
        request = tornado.httpclient.HTTPRequest(
            f"{base}{self.request.uri}",
            method=method,
            headers=self._upstream_headers(),
            body=self.request.body if method not in ("GET", "HEAD", "OPTIONS") else None,
            follow_redirects=False,
            decompress_response=False,
            allow_nonstandard_methods=True,
            connect_timeout=10,
            # No overall limit: event streams and long polls stay open as long as the UI wants.
            request_timeout=0,
            header_callback=relay.on_header_line,
            streaming_callback=relay.on_chunk,
        )
        try:
            resp = await tornado.httpclient.AsyncHTTPClient().fetch(request, raise_error=False)
            failed = resp.code == 599
        except OSError as exc:
            logger.warning("UI '%s' upstream failed: %s", self.ui_name, exc)
            failed = True
        if failed and not relay.started:
            self.set_status(502)
            self.finish(f"UI '{self.ui_name}' did not answer")
            return
        self.finish()

    # Websockets

    @override
    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        self._client_subprotocols = subprotocols
        return subprotocols[0] if subprotocols else None

    @override
    async def open(self, *args: Any, **kwargs: Any) -> None:
        base = self._upstream_base()
        if base is None:
            self.close(1013, "UI is starting or unavailable")
            return
        ws_base = "ws" + base[len("http") :]
        request = tornado.httpclient.HTTPRequest(
            f"{ws_base}{self.request.uri}", headers=self._upstream_headers()
        )
        try:
            self._upstream_ws = await tornado.websocket.websocket_connect(
                request, subprotocols=self._client_subprotocols
            )
        except (OSError, tornado.httpclient.HTTPClientError) as exc:
            logger.warning("UI '%s' websocket upstream failed: %s", self.ui_name, exc)
            self.close(1011, "UI did not accept the connection")
            return
        if self.auth is not None and self.auth.session_secret:
            sockets: UiSockets = self.application.settings["ui_sockets"]
            sockets.add(self.auth.session_secret, self)
        self._relay = asyncio.get_running_loop().create_task(self._relay_upstream())

    async def _relay_upstream(self) -> None:
        upstream = self._upstream_ws
        assert upstream is not None
        while True:
            message = await upstream.read_message()
            if message is None:
                self.close()
                return
            try:
                await self.write_message(message, binary=isinstance(message, bytes))
            except tornado.websocket.WebSocketClosedError:
                return

    @override
    async def on_message(self, message: str | bytes) -> None:
        if self._upstream_ws is not None:
            await self._upstream_ws.write_message(message, binary=isinstance(message, bytes))

    @override
    def on_close(self) -> None:
        if self._upstream_ws is not None:
            self._upstream_ws.close()
        if self.auth is not None and self.auth.session_secret:
            sockets: UiSockets = self.application.settings["ui_sockets"]
            sockets.remove(self.auth.session_secret, self)
