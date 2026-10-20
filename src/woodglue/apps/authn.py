"""
Request authentication shared by the woodglue HTTP handlers.

`authenticate` turns a request into a `woodglue.access.Caller`, reading the
application settings `auth_enabled` and `auth_store`. An app with auth disabled,
or built without an `auth_store` (as `create_app` does when the config has no
`storage.auth_db`), serves every request as the anonymous Caller.

A request authenticates in one of these ways, tried in order:

- `Authorization: Bearer <token>`, for the CLI, scripts, and LLM tooling.
- An identity asserted by a trusted reverse proxy (see
  `woodglue.apps.identity_providers.TrustedProxy`). The first valid assertion
  signs the person in and sets a Session cookie; later requests reuse that
  Session while the proxy keeps asserting the same person.
- The `wgl_session` cookie, set by `/auth/login` for browsers. It is HttpOnly,
  so no page script (including a project UI) can read it (see `docs/adr/0003`).

The last two arrive automatically with any browser request, so both count as
cookie authentication, and state-changing handlers must call `is_same_origin`
for them.

Tokens in query strings are not accepted, since URLs end up in logs, browser
history, and `Referer` headers.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlsplit

import tornado.httputil
import tornado.web

from woodglue.access import ANONYMOUS, Caller
from woodglue.apps.identity_providers import SignInError, TrustedProxy, admits, role_sources
from woodglue.auth_store import AuthStore, UserDisabled

logger = logging.getLogger(__name__)

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
    proxied = _proxy_authentication(handler, store)
    if proxied is not None:
        return proxied
    secret = handler.get_cookie(SESSION_COOKIE)
    if secret:
        return Authentication(store.resolve(secret), AuthVia.cookie, secret)
    return Authentication(None, AuthVia.anonymous)


def _proxy_authentication(
    handler: tornado.web.RequestHandler, store: AuthStore
) -> Authentication | None:
    """Authentication from a trusted proxy's assertion, or `None` if there is none."""
    settings = handler.application.settings
    proxies: list[TrustedProxy] = settings.get("trusted_proxies", [])
    for proxy in proxies:
        try:
            who = proxy.identify(handler.request)
        except SignInError as exc:
            logger.warning("Rejected proxy assertion: %s", exc)
            return Authentication(None, AuthVia.cookie)
        if who is None:
            continue
        secret = handler.get_cookie(SESSION_COOKIE)
        if secret:
            caller = store.resolve(secret)
            if caller is not None and caller.user_id == store.linked_user(
                who.provider, who.subject
            ):
                return Authentication(caller, AuthVia.cookie, secret)
        if not admits(proxy.config.admit, who):
            logger.info("Proxy sign-in refused for %s (%s)", who.subject, who.email)
            return Authentication(None, AuthVia.cookie)
        admins: list[str] = settings["config"].auth.admins
        try:
            user = store.sign_in_person(
                who.provider,
                who.subject,
                email=who.email,
                email_verified=who.email_verified,
                name=who.name,
                role_sources=role_sources(proxy.config, who, admins),
            )
        except UserDisabled:
            return Authentication(None, AuthVia.cookie)
        secret = store.create_session(user.id)
        set_session_cookie(handler, secret)
        logger.info("Signed in %s (%s) through proxy %s", user.id, user.email, who.provider)
        return Authentication(store.resolve(secret), AuthVia.cookie, secret)
    return None


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
