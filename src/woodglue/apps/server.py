"""Tornado application factory for the woodglue JSON-RPC server."""

from __future__ import annotations

import tornado.web
from lythonic.compose.namespace import Namespace

from woodglue.apps.docs import DocsHandler, DocsUiHandler
from woodglue.apps.rpc import JsonRpcHandler


def create_app(namespace: Namespace) -> tornado.web.Application:
    """Build a Tornado Application with JSON-RPC, docs, and docs-UI routes.

    The *namespace* is stored in ``application.settings["namespace"]``
    so handlers can look it up at request time.
    """
    return tornado.web.Application(
        [
            (r"/rpc", JsonRpcHandler),
            (r"/docs", DocsHandler),
            (r"/docs/ui", DocsUiHandler),
        ],
        namespace=namespace,
    )
