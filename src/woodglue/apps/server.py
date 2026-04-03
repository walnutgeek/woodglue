"""Tornado application factory for the woodglue JSON-RPC server."""

from __future__ import annotations

from typing import Any

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

    handlers: list[Any] = [
        (r"/rpc", JsonRpcHandler),
    ]

    if config.docs.enabled:
        from woodglue.apps.llm_docs import LlmsTxtHandler, MethodDocHandler, OpenApiHandler

        handlers.append((r"/docs/llms\.txt", LlmsTxtHandler))
        handlers.append((r"/docs/methods/(.+)", MethodDocHandler))
        if config.docs.openapi:
            handlers.append((r"/docs/openapi\.json", OpenApiHandler))

    return tornado.web.Application(
        handlers,
        namespaces=namespaces,
        config=config,
    )
