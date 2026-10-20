"""Tornado application factory for the woodglue JSON-RPC server."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import tornado.web
from lythonic.compose.namespace import Namespace

from woodglue.apps.identity_providers import PendingSignIns, ProviderClient, make_clients
from woodglue.apps.llm_docs import build_method_index
from woodglue.apps.rpc import JsonRpcHandler
from woodglue.apps.uis import UiStaticHandler, ui_path
from woodglue.auth_store import AuthStore
from woodglue.config import NamespaceEntry, WoodglueConfig
from woodglue.engine import EngineRegistry
from woodglue.mount import MountContext


def create_app(
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]],
    config: WoodglueConfig | None = None,
    engine_registry: EngineRegistry | None = None,
    mounts: dict[str, MountContext] | None = None,
    auth_store: AuthStore | None = None,
) -> tornado.web.Application:
    """
    Build a Tornado Application with JSON-RPC and optional docs/UI routes.

    `namespaces` maps prefix strings to `(Namespace, NamespaceEntry)` tuples.
    The plain `Namespace` dict (all namespaces) is stored in app settings for
    internal use. The `method_index` is filtered by `expose_api`.

    With auth enabled, `auth_store` defaults to one opened on
    `config.storage.auth_db`; pass it to share the instance the system
    namespace uses.
    """
    if config is None:
        config = WoodglueConfig(namespaces={})
    if auth_store is None and config.auth.enabled and config.storage.auth_db is not None:
        auth_store = AuthStore(config.storage.auth_db, config.auth.resolved_roles())

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

    login_providers: Mapping[str, ProviderClient] = {}
    if auth_store is not None:
        from woodglue.apps.auth_pages import (
            LoginHandler,
            LogoutHandler,
            SignInCallbackHandler,
            StartSignInHandler,
        )

        login_providers = make_clients(config.auth.providers)
        handlers.append((r"/auth/login", LoginHandler))
        handlers.append((r"/auth/logout", LogoutHandler))
        handlers.append((r"/auth/start/([^/]+)", StartSignInHandler))
        handlers.append((r"/auth/callback/([^/]+)", SignInCallbackHandler))

    if config.ui.enabled:
        console_dist = Path(__file__).resolve().parent.parent / "ui" / "dist"
        if console_dist.is_dir():
            handlers.append((r"/console", tornado.web.RedirectHandler, {"url": "/console/"}))
            handlers.append(
                (
                    r"/console/(.*)",
                    UiStaticHandler,
                    {"path": str(console_dist), "default_filename": "index.html"},
                )
            )
        # `/ui/` was the Console's path before 0.0.9; keep old bookmarks working.
        landing = ui_path(config.ui.default)
        handlers.append((r"/(ui/?)?", tornado.web.RedirectHandler, {"url": landing}))

    return tornado.web.Application(
        handlers,
        namespaces=plain_namespaces,
        method_index=method_index,
        config=config,
        auth_enabled=config.auth.enabled,
        auth_store=auth_store,
        login_providers=login_providers,
        pending_sign_ins=PendingSignIns(),
        engine_registry=engine_registry,
        mounts=mounts or {},
    )
