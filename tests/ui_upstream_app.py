"""
A tiny server standing in for a Process-backed UI in tests.

Run as `python ui_upstream_app.py <port> <base_path>`. Under the base path:

- `GET /_health`: readiness probe.
- `GET /info`: the request it received (path, headers, cookies) as JSON.
- `GET /crash`: exits with status 3 right after answering.
- `GET /stream`: sends `first`, then `second` only after `GET /release`.
- `/ws`: a websocket that first sends the handshake headers as JSON, then
  echoes every message.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys

import tornado.web
import tornado.websocket
from typing_extensions import override


class Health(tornado.web.RequestHandler):
    @override
    def get(self) -> None:
        self.write("ok")


class Info(tornado.web.RequestHandler):
    @override
    def get(self) -> None:
        self.write(
            {
                "path": self.request.uri,
                "headers": dict(self.request.headers),
                "cookies": {k: v.value for k, v in self.request.cookies.items()},
                "env_woodglue_url": os.environ.get("WOODGLUE_URL"),
            }
        )


class Crash(tornado.web.RequestHandler):
    @override
    def get(self) -> None:
        self.write("bye")
        self.finish()
        asyncio.get_running_loop().call_later(0.05, os._exit, 3)


RELEASE = asyncio.Event()


class Stream(tornado.web.RequestHandler):
    @override
    async def get(self) -> None:
        self.write("first\n")
        await self.flush()
        await RELEASE.wait()
        RELEASE.clear()
        self.write("second\n")


class Release(tornado.web.RequestHandler):
    @override
    def get(self) -> None:
        RELEASE.set()


class Echo(tornado.websocket.WebSocketHandler):
    @override
    def select_subprotocol(self, subprotocols: list[str]) -> str | None:
        return subprotocols[0] if subprotocols else None

    @override
    def open(self, *args: str, **kwargs: str) -> None:
        self.write_message(json.dumps({"headers": dict(self.request.headers)}))

    @override
    def on_message(self, message: str | bytes) -> None:
        self.write_message(message, binary=isinstance(message, bytes))


async def main(port: int, base: str) -> None:
    app = tornado.web.Application(
        [
            (rf"{base}/_health", Health),
            (rf"{base}/info", Info),
            (rf"{base}/crash", Crash),
            (rf"{base}/stream", Stream),
            (rf"{base}/release", Release),
            (rf"{base}/ws", Echo),
        ]
    )
    app.listen(port, "127.0.0.1")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]), sys.argv[2]))
