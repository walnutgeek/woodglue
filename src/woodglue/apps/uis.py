"""
Serving the Console and project UIs behind the `view` Grant.

Every UI has a name, and opening it needs `view` on `ui:<name>`; the Console is
the UI named `console`, served at `/console/`, and project UIs are served at
`/ui/<name>/` (see `woodglue.config.UiEntry`). A page load without a Session
redirects to `/auth/login?next=<path>`, and one without the Grant gets 403. A
UI marked `public` skips both checks; the API it calls still needs a Caller.
"""

from __future__ import annotations

import os
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
