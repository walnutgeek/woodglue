"""Tornado application factory for the woodglue JSON-RPC server."""

from __future__ import annotations

import tornado.web
from lythonic.compose.namespace import Namespace

from woodglue.apps.rpc import JsonRpcHandler
from woodglue.config import WoodglueConfig


def create_app(
    namespaces: dict[str, Namespace],
    config: WoodglueConfig | None = None,
) -> tornado.web.Application:
    """
    Build a Tornado Application with JSON-RPC and optional docs/UI routes.

    `namespaces` maps prefix strings to loaded Namespace instances.
    """
    if config is None:
        config = WoodglueConfig(namespaces={})

    handlers: list[tuple[str, type[tornado.web.RequestHandler]]] = [
        (r"/rpc", JsonRpcHandler),
    ]

    return tornado.web.Application(
        handlers,
        namespaces=namespaces,
        config=config,
    )
