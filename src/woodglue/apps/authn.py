"""
Request authentication shared by the woodglue HTTP handlers.

`request_caller` turns a request into a `woodglue.access.Caller`, reading the
application settings `auth_enabled` and `auth_store`. An app with auth disabled,
or built without an `auth_store` (as `create_app` does when the config has no
`storage.auth_db`), serves every request as the anonymous Caller.

Credentials come only from the `Authorization: Bearer` header; tokens in query
strings are not accepted, since URLs end up in logs, browser history, and
`Referer` headers.
"""

from __future__ import annotations

import tornado.web

from woodglue.access import ANONYMOUS, Caller
from woodglue.auth_store import AuthStore


def bearer_token(request: tornado.httputil.HTTPServerRequest) -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


def request_caller(handler: tornado.web.RequestHandler) -> Caller | None:
    """The Caller of this request, or `None` if it is not authenticated."""
    settings = handler.application.settings
    store: AuthStore | None = settings.get("auth_store")
    if not settings.get("auth_enabled", False) or store is None:
        return ANONYMOUS
    token = bearer_token(handler.request)
    return store.resolve(token) if token else None
