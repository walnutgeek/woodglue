"""
Tornado application factory for the woodglue JSON-RPC server.

Secrets in query strings stay out of the logs: `create_app` writes the access log
through `log_request` and filters the two Tornado loggers that record a failed
request's URI (`tornado.general` and `tornado.application`), so every route gets
`redact_queries`, including routes added later.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from urllib.parse import unquote_plus

import tornado.ioloop
import tornado.log
import tornado.web
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.apps.auth_pages import CliCodes
from woodglue.apps.identity_providers import (
    PendingSignIns,
    ProviderClient,
    make_clients,
    make_trusted_proxies,
)
from woodglue.apps.llm_docs import build_method_index
from woodglue.apps.rpc import JsonRpcHandler
from woodglue.apps.ui_process import UiProcesses
from woodglue.apps.ui_proxy import UiProxyHandler, UiSockets
from woodglue.apps.uis import UiStaticHandler, VerifyHandler, ui_path
from woodglue.auth_store import AuthStore
from woodglue.config import AuthConfig, NamespaceEntry, WoodglueConfig
from woodglue.engine import EngineRegistry
from woodglue.mount import MountContext

logger = logging.getLogger(__name__)

_SECRET_PARAMS = frozenset({"code", "state", "token"})
_REDACTED = "…"
# Up to a space, the only thing that ends a URI in a request line, so a quote or comma
# after one can never end a value early.
_QUERY = re.compile(r"\?[^ ]*")


def _redact_param(param: str) -> str:
    raw_name, _, value = param.partition("=")
    # Names are compared decoded, as `get_argument` sees them.
    name = unquote_plus(raw_name)
    # `next` holds the URL being returned to, whose own query can hold a secret:
    # `/auth/cli` sends its `state` through login this way.
    if name in _SECRET_PARAMS or (name == "next" and "?" in unquote_plus(value)):
        return f"{raw_name}={_REDACTED}"
    return param


def redact_queries(text: str) -> str:
    """
    Hide the values of secret query parameters in `text`, keeping their names.

    >>> redact_queries("GET /auth/callback/okta?code=abc&state=def (127.0.0.1)")
    'GET /auth/callback/okta?code=…&state=… (127.0.0.1)'
    >>> redact_queries("/auth/cli?state=a&state=b&code_challenge=c&redirect_uri=d")
    '/auth/cli?state=…&state=…&code_challenge=c&redirect_uri=d'
    >>> redact_queries("/auth/login?token=&next=/console/")
    '/auth/login?token=…&next=/console/'
    >>> redact_queries("/auth/login?next=%2Fauth%2Fcli%3Fstate%3Dabc")
    '/auth/login?next=…'
    >>> redact_queries("/x?%74oken=abc"), redact_queries("/x?a=\\xa0&code=abc")
    ('/x?%74oken=…', '/x?a=\\xa0&code=…')
    """
    return _QUERY.sub(lambda m: "?" + "&".join(map(_redact_param, m[0][1:].split("&"))), text)


def log_request(handler: tornado.web.RequestHandler) -> None:
    """Tornado's default access log line and levels, with `redact_queries` applied."""
    status = handler.get_status()
    if status < 400:
        log_method = tornado.log.access_log.info
    elif status < 500:
        log_method = tornado.log.access_log.warning
    else:
        log_method = tornado.log.access_log.error
    request = handler.request
    log_method(
        "%d %s %.2fms",
        status,
        redact_queries(f"{request.method} {request.uri} ({request.remote_ip})"),
        1000.0 * request.request_time(),
    )


class _RedactQueriesFilter(logging.Filter):
    @override
    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_queries(message)
        if redacted != message:
            record.msg, record.args = redacted, None
        return True


# One instance, so that `addFilter` keeps repeated `create_app` calls from stacking it.
_redact_queries_filter = _RedactQueriesFilter()


def create_app(
    namespaces: dict[str, tuple[Namespace, NamespaceEntry]],
    config: WoodglueConfig | None = None,
    engine_registry: EngineRegistry | None = None,
    mounts: dict[str, MountContext] | None = None,
    auth_store: AuthStore | None = None,
    ui_processes: UiProcesses | None = None,
) -> tornado.web.Application:
    """
    Build a Tornado Application with JSON-RPC and optional docs/UI routes.

    `namespaces` maps prefix strings to `(Namespace, NamespaceEntry)` tuples.
    The plain `Namespace` dict (all namespaces) is stored in app settings for
    internal use. The `method_index` is filtered by `expose_api`.

    With auth enabled, `auth_store` defaults to one opened on
    `config.storage.auth_db`, which must then be set; pass it to share the
    instance the system namespace uses. Without `config`, auth is disabled. `ui_processes` supervises Process-backed UIs; the caller
    starts and stops it (see `wgl start`).
    """
    if config is None:
        config = WoodglueConfig(namespaces={}, auth=AuthConfig(enabled=False))
    if auth_store is None and config.auth.enabled:
        if config.storage.auth_db is None:
            raise ValueError("Auth is enabled but storage.auth_db is not set")
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
            CliAuthorizeHandler,
            CliTokenHandler,
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
        handlers.append((r"/auth/verify", VerifyHandler))
        handlers.append((r"/auth/cli", CliAuthorizeHandler))
        handlers.append((r"/auth/cli/token", CliTokenHandler))

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

    if ui_processes is None:
        ui_processes = UiProcesses(config.uis, "")
    for name, entry in config.uis.items():
        handlers.append((rf"/ui/{name}", tornado.web.RedirectHandler, {"url": f"/ui/{name}/"}))
        if not entry.is_static:
            handlers.append(
                (rf"/ui/{name}/.*", UiProxyHandler, {"ui_name": name, "public": entry.public})
            )
            continue
        assert entry.path is not None
        if not Path(entry.path).is_dir():
            logger.warning("UI '%s': directory %s does not exist", name, entry.path)
        handlers.append(
            (
                rf"/ui/{name}/(.*)",
                UiStaticHandler,
                {
                    "path": entry.path,
                    "default_filename": "index.html",
                    "ui_name": name,
                    "spa_fallback": entry.spa_fallback,
                    "public": entry.public,
                },
            )
        )

    if config.ui.enabled or config.uis:
        # Temporary (302): the target follows `ui.default`. `/ui/` is covered because
        # releases up to 0.0.8 sent `/` there with a 301, which browsers keep cached.
        handlers.append(
            (
                r"/(ui/?)?",
                tornado.web.RedirectHandler,
                {"url": ui_path(config.ui.default), "permanent": False},
            )
        )

    trusted_proxies = make_trusted_proxies(config.auth.providers) if auth_store else []
    for proxy in trusted_proxies:
        tornado.ioloop.IOLoop.current().add_callback(proxy.load_keys)

    # `RequestHandler.log_exception` writes the full URI here, on every kind of handler.
    tornado.log.gen_log.addFilter(_redact_queries_filter)
    tornado.log.app_log.addFilter(_redact_queries_filter)

    return tornado.web.Application(
        handlers,
        log_function=log_request,
        namespaces=plain_namespaces,
        method_index=method_index,
        config=config,
        auth_enabled=config.auth.enabled,
        auth_store=auth_store,
        login_providers=login_providers,
        trusted_proxies=trusted_proxies,
        pending_sign_ins=PendingSignIns(),
        cli_codes=CliCodes(),
        ui_processes=ui_processes,
        ui_sockets=UiSockets(),
        engine_registry=engine_registry,
        mounts=mounts or {},
    )
