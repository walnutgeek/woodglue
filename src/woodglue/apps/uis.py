"""
Serving the Console and project UIs behind the `view` Grant.

Every UI has a name, and opening it needs `view` on `ui:<name>`; the Console is
the UI named `console`, served at `/console/`, and project UIs are served at
`/ui/<name>/` (see `woodglue.config.UiEntry`). A page load without a Session
redirects to `/auth/login?next=<path>`, and one without the Grant gets 403. A
UI marked `public` skips both checks; the API it calls still needs a Caller.
"""

from __future__ import annotations

import json
import os
from urllib.parse import quote

import tornado.web
from typing_extensions import override

from woodglue.access import Action
from woodglue.apps.authn import Authentication, AuthVia, authenticate
from woodglue.auth_store import AuthStore

CONSOLE = "console"


def ui_path(name: str) -> str:
    """
    The URL path where a UI is served.

    >>> ui_path("console"), ui_path("reports")
    ('/console/', '/ui/reports/')
    """
    return "/console/" if name == CONSOLE else f"/ui/{name}/"


USER_HEADER = "X-Woodglue-User"
TOKEN_HEADER = "X-Woodglue-Token"


def identity_headers(auth: Authentication, store: AuthStore | None, ui_name: str) -> dict[str, str]:
    """
    The headers that tell a UI's server who is viewing (see
    `woodglue.ui_support`): the User as JSON, and for a browser Session a
    Delegated Token for this UI.
    """
    headers: dict[str, str] = {}
    caller = auth.caller
    if caller is not None:
        headers[USER_HEADER] = json.dumps(
            {
                "id": caller.user_id,
                "name": caller.name,
                "email": caller.email,
                "roles": list(caller.roles),
            }
        )
    if store is not None and auth.via == AuthVia.cookie and auth.session_secret:
        try:
            headers[TOKEN_HEADER] = store.delegate(auth.session_secret, ui_name)
        except ValueError:
            pass
    return headers


def require_view(handler: tornado.web.RequestHandler, ui_name: str) -> Authentication | None:
    """
    Check that the request may open `ui_name`, finishing it with a login
    redirect or 403 if not. Returns the request's `Authentication`, or `None`
    once the request is finished.
    """
    auth = authenticate(handler)
    if auth.caller is None:
        handler.redirect(f"/auth/login?next={quote(handler.request.uri or '/', safe='')}")
        return None
    if not auth.caller.can(Action.view, f"ui:{ui_name}"):
        handler.set_status(403)
        handler.finish(f"Forbidden: requires 'view' on 'ui:{ui_name}'")
        return None
    return auth


class UiStaticHandler(tornado.web.StaticFileHandler):
    """Static files of one UI, gated by `require_view` unless public."""

    ui_name: str = CONSOLE
    spa_fallback: bool = False
    public: bool = False

    @override
    def initialize(
        self,
        path: str,
        default_filename: str | None = None,
        ui_name: str = CONSOLE,
        spa_fallback: bool = False,
        public: bool = False,
    ) -> None:
        super().initialize(path, default_filename)
        self.ui_name = ui_name
        self.spa_fallback = spa_fallback
        self.public = public

    @override
    def prepare(self) -> None:
        if not self.public:
            require_view(self, self.ui_name)

    @override
    def validate_absolute_path(self, root: str, absolute_path: str) -> str | None:
        # Client-side routes like `/runs/42` have no file; serve the app's index.html.
        if (
            self.spa_fallback
            and not os.path.exists(absolute_path)
            and not os.path.splitext(absolute_path)[1]
            and self.default_filename
        ):
            absolute_path = os.path.join(root, self.default_filename)
        return super().validate_absolute_path(root, absolute_path)


class VerifyHandler(tornado.web.RequestHandler):
    """
    GET `/auth/verify`: access check for a reverse proxy's `forward_auth`
    (see `docs/adr/0004`). The UI is `?ui=<name>`, or taken from the
    `X-Forwarded-Uri` the proxy sends (`/ui/<name>/...` or `/console/...`).

    Answers 200 with the identity headers for an allowed request, a redirect to
    the login page (returning to the original URI) without a Session, 403
    without the `view` Grant, and 400 if no UI can be named.
    """

    @override
    def get(self) -> None:
        uri = self.request.headers.get("X-Forwarded-Uri", "/")
        name = self.get_argument("ui", None) or _ui_name_from_path(uri)
        if name is None:
            raise tornado.web.HTTPError(400, "No UI named by ?ui= or X-Forwarded-Uri")
        settings = self.application.settings
        entry = settings["config"].uis.get(name)
        auth = authenticate(self)
        if auth.caller is None and not (entry is not None and entry.public):
            self.redirect(f"/auth/login?next={quote(uri, safe='')}")
            return
        public = entry is not None and entry.public
        if (
            not public
            and auth.caller is not None
            and not auth.caller.can(Action.view, f"ui:{name}")
        ):
            raise tornado.web.HTTPError(403)
        for header, value in identity_headers(auth, settings.get("auth_store"), name).items():
            self.set_header(header, value)
        self.set_status(200)


def _ui_name_from_path(path: str) -> str | None:
    """
    >>> _ui_name_from_path("/ui/reports/runs/1"), _ui_name_from_path("/console/x")
    ('reports', 'console')
    >>> _ui_name_from_path("/rpc") is None
    True
    """
    parts = path.split("?", 1)[0].split("/")
    if len(parts) > 2 and parts[1] == "ui" and parts[2]:
        return parts[2]
    if len(parts) > 1 and parts[1] == CONSOLE:
        return CONSOLE
    return None
