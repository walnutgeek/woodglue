"""
Serving the Console and other UIs behind the `view` Grant.

Every UI has a name, and opening it needs `view` on `ui:<name>`; the Console is
the UI named `console`, served at `/console/`. A page load without a Session
redirects to `/auth/login?next=<path>`, and one without the Grant gets 403.
"""

from __future__ import annotations

from urllib.parse import quote

import tornado.web
from typing_extensions import override

from woodglue.access import Action
from woodglue.apps.authn import authenticate

CONSOLE = "console"


def ui_path(name: str) -> str:
    """
    The URL path where a UI is served.

    >>> ui_path("console"), ui_path("reports")
    ('/console/', '/ui/reports/')
    """
    return "/console/" if name == CONSOLE else f"/ui/{name}/"


def require_view(handler: tornado.web.RequestHandler, ui_name: str) -> bool:
    """
    Check that the request may open `ui_name`, finishing it with a login
    redirect or 403 if not. Returns whether the handler should go on.
    """
    caller = authenticate(handler).caller
    if caller is None:
        handler.redirect(f"/auth/login?next={quote(handler.request.uri or '/', safe='')}")
        return False
    if not caller.can(Action.view, f"ui:{ui_name}"):
        handler.set_status(403)
        handler.finish(f"Forbidden: requires 'view' on 'ui:{ui_name}'")
        return False
    return True


class UiStaticHandler(tornado.web.StaticFileHandler):
    """Static files of one UI, gated by `require_view`."""

    ui_name: str = CONSOLE

    @override
    def initialize(
        self, path: str, default_filename: str | None = None, ui_name: str = CONSOLE
    ) -> None:
        super().initialize(path, default_filename)
        self.ui_name = ui_name

    @override
    def prepare(self) -> None:
        require_view(self, self.ui_name)
