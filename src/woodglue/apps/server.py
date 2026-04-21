"""Tornado application factory for the woodglue JSON-RPC server."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import tornado.web
from lythonic.compose.namespace import Namespace

from woodglue.apps.llm_docs import build_method_index
from woodglue.apps.rpc import JsonRpcHandler
from woodglue.config import NamespaceEntry, WoodglueConfig


def create_app(
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]],
    config: WoodglueConfig | None = None,
) -> tornado.web.Application:
    """
    Build a Tornado Application with JSON-RPC and optional docs/UI routes.

    `namespaces` maps prefix strings to `(Namespace, NamespaceEntry)` tuples.
    The plain `Namespace` dict (all namespaces) is stored in app settings for
    internal use. The `method_index` is filtered by `expose_api`.
    """
    if config is None:
        config = WoodglueConfig(namespaces={})

    # Plain namespace dict for internal use (e.g. `wgl run`)
    plain_namespaces = {prefix: ns for prefix, (ns, _) in namespaces.items()}

    method_index = build_method_index(namespaces)

    handlers: list[Any] = [
        (r"/rpc", JsonRpcHandler),
    ]

    if config.docs.enabled:
        from woodglue.apps.llm_docs import LlmsTxtHandler, MethodDocHandler, OpenApiHandler

        handlers.append((r"/docs/llms\.txt", LlmsTxtHandler))
        handlers.append((r"/docs/methods/(.+)", MethodDocHandler))
        if config.docs.openapi:
            handlers.append((r"/docs/openapi\.json", OpenApiHandler))

    if config.ui.enabled:
        ui_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
        if ui_dist.is_dir():
            handlers.append(
                (
                    r"/ui/(.*)",
                    tornado.web.StaticFileHandler,
                    {"path": str(ui_dist), "default_filename": "index.html"},
                )
            )

    return tornado.web.Application(
        handlers,
        namespaces=plain_namespaces,
        method_index=method_index,
        config=config,
        auth_enabled=config.auth.enabled,
        auth_db=config.storage.auth_db,
    )
