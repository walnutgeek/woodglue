"""
Request authentication shared by the woodglue HTTP handlers.

`authenticate` turns a request into a `woodglue.access.Caller`, reading the
application settings `auth_enabled` and `auth_store`. An app with auth disabled,
or built without an `auth_store` (as `create_app` does when the config has no
`storage.auth_db`), serves every request as the anonymous Caller.

A request authenticates in one of two ways:

- `Authorization: Bearer <token>`, for the CLI, scripts, and LLM tooling.
- The `wgl_session` cookie, set by `/auth/login` for browsers. It is HttpOnly,
  so no page script (including a project UI) can read it (see `docs/adr/0003`).
  Because browsers send it automatically, state-changing handlers must call
  `is_same_origin` for cookie-authenticated requests.

Tokens in query strings are not accepted, since URLs end up in logs, browser
history, and `Referer` headers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

import tornado.httputil
import tornado.web

from woodglue.access import ANONYMOUS, Caller
from woodglue.auth_store import AuthStore

SESSION_COOKIE = "wgl_session"


class AuthVia(StrEnum):
    anonymous = "anonymous"
    bearer = "bearer"
    cookie = "cookie"


@dataclass(frozen=True)
class Authentication:
    """The Caller of a request (`None` if unauthenticated) and how it authenticated."""

    caller: Caller | None
    via: AuthVia
    session_secret: str | None = None


def bearer_token(request: tornado.httputil.HTTPServerRequest) -> str:
    header = request.headers.get("Authorization", "")
    return header[7:].strip() if header.startswith("Bearer ") else ""


def authenticate(handler: tornado.web.RequestHandler) -> Authentication:
    settings = handler.application.settings
    store: AuthStore | None = settings.get("auth_store")
    if not settings.get("auth_enabled", False) or store is None:
        return Authentication(ANONYMOUS, AuthVia.anonymous)
    token = bearer_token(handler.request)
    if token:
        return Authentication(store.resolve(token), AuthVia.bearer)
    secret = handler.get_cookie(SESSION_COOKIE)
    if secret:
        return Authentication(store.resolve(secret), AuthVia.cookie, secret)
    return Authentication(None, AuthVia.anonymous)


def request_caller(handler: tornado.web.RequestHandler) -> Caller | None:
    """The Caller of this request, or `None` if it is not authenticated."""
    return authenticate(handler).caller


def is_same_origin(request: tornado.httputil.HTTPServerRequest) -> bool:
    """
    Whether a browser request comes from this server's own pages. Uses `Origin`,
    falling back to `Referer`; a request with neither is not a browser
    cross-site request and passes.
    """
    source = request.headers.get("Origin") or request.headers.get("Referer")
    if source is None:
        return True
    return urlsplit(source).netloc == request.host


def set_session_cookie(handler: tornado.web.RequestHandler, secret: str) -> None:
    handler.set_cookie(
        SESSION_COOKIE,
        secret,
        path="/",
        httponly=True,
        samesite="Lax",
        secure=handler.request.protocol == "https",
    )


def clear_session_cookie(handler: tornado.web.RequestHandler) -> None:
    handler.clear_cookie(SESSION_COOKIE, path="/")
