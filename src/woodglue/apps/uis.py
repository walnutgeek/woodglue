"""
Serving the Console and project UIs behind the `view` Grant.

Every UI has a name, and opening it needs `view` on `ui:<name>`; the Console is
the UI named `console`, served at `/console/`, and project UIs are served at
`/ui/<name>/` (see `woodglue.config.UiEntry`). A page load without a Session
redirects to `/auth/login?next=<path>`, and one without the Grant gets 403. A
UI marked `public` skips both checks; the API it calls still needs a Caller.

The 403 is a bare message naming the missing Grant, unless a User signed in
with a Session has no Grants at all (e.g. just signed up with
`default_roles: []`, or `pending`): then it is a page saying who is signed in
and that the account is awaiting approval (if `pending`) or has no access yet,
with Sign out.
"""

from __future__ import annotations

import html
import json
import os
from textwrap import dedent
from urllib.parse import quote

import tornado.web
from typing_extensions import override

from woodglue.access import Action, Caller
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


def identity_headers(
    auth: Authentication,
    store: AuthStore | None,
    ui_name: str,
    grants: list[str] | None = None,
) -> dict[str, str]:
    """
    The headers that tell a UI's server who is viewing (see
    `woodglue.ui_support`): the User as JSON, and for a browser Session a
    Delegated Token for this UI, narrowed to the UI's `grants`.
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
            headers[TOKEN_HEADER] = store.delegate(auth.session_secret, ui_name, grants)
        except ValueError:
            pass
    return headers


PAGE_STYLE = dedent("""
    :root { color-scheme: light dark; --bg: #f6f6f4; --fg: #1d1d1b; --muted: #6b6b66;
            --card: #ffffff; --border: #dcdcd6; --accent: #b4632d; --error: #b3261e; }
    @media (prefers-color-scheme: dark) {
      :root { --bg: #141413; --fg: #ecece8; --muted: #9a9a93; --card: #1e1e1c;
              --border: #34342f; --accent: #e08a4f; --error: #f2b8b5; }
    }
    body { margin: 0; min-height: 100vh; display: grid; place-items: center;
           background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }
    main { width: min(420px, calc(100vw - 32px)); background: var(--card);
           border: 1px solid var(--border); border-radius: 10px; padding: 28px; }
    h1 { font-size: 20px; margin: 0 0 4px; }
    p { color: var(--muted); margin: 0 0 20px; }
    label { display: block; font-size: 13px; margin-bottom: 6px; }
    input { box-sizing: border-box; width: 100%; padding: 10px 12px; font: 13px monospace;
            border: 1px solid var(--border); border-radius: 6px; background: var(--bg);
            color: var(--fg); }
    button { margin-top: 16px; width: 100%; padding: 10px; border: 0; border-radius: 6px;
             background: var(--accent); color: #fff; font: inherit; cursor: pointer; }
    .error { color: var(--error); margin: 0 0 16px; }
    .providers { display: grid; gap: 8px; margin-bottom: 20px; }
    .providers a { display: block; text-align: center; padding: 10px; border-radius: 6px;
                   border: 1px solid var(--border); color: var(--fg); text-decoration: none; }
    .divider { text-align: center; color: var(--muted); font-size: 12px; margin: 4px 0 16px; }
    """).strip()
"""CSS shared by woodglue's own pages: sign-in and no-Grants."""


_NO_GRANTS_PAGE = dedent("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>{heading} - woodglue</title>
      <style>{style}</style>
    </head>
    <body>
      <main>
        <h1>{heading}</h1>
        <p>{message}</p>
        <p>Signed in as <b>{name}</b>{email}.</p>
        <form method="post" action="/auth/logout">
          <button type="submit">Sign out</button>
        </form>
      </main>
    </body>
    </html>
    """).strip()


def _no_grants_text(caller: Caller) -> tuple[str, str]:
    """The heading and message of the no-Grants page for `caller`."""
    if caller.pending:
        return "Awaiting approval", "Your account is awaiting approval by an admin."
    return "No access yet", "Your account has no access yet; ask an admin to give you a Role."


def _no_grants_page(caller: Caller) -> str:
    """
    The page for a signed-in User with no Grants at all: who is signed in, why
    nothing opens, and a Sign out button.
    """
    heading, message = _no_grants_text(caller)
    return _NO_GRANTS_PAGE.format(
        style=PAGE_STYLE,
        heading=html.escape(heading),
        message=html.escape(message),
        name=html.escape(caller.name),
        email=f" ({html.escape(caller.email)})" if caller.email else "",
    )


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
        _forbid_view(handler, auth.caller, auth.via, ui_name)
        return None
    return auth


def _forbid_view(
    handler: tornado.web.RequestHandler, caller: Caller, via: AuthVia, ui_name: str
) -> None:
    """Finish the request with 403 for a Caller lacking `view` on `ui_name`."""
    handler.set_status(403)
    # A User with some Grants has access and opened the wrong UI; one signed
    # in with none (e.g. just signed up) needs to know to ask an admin.
    if caller.has_grants or via != AuthVia.cookie:
        handler.finish(f"Forbidden: requires 'view' on 'ui:{ui_name}'")
    else:
        handler.set_header("Content-Type", "text/html; charset=UTF-8")
        handler.finish(_no_grants_page(caller))


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
    without the `view` Grant (the same page or message as `require_view`; the
    proxy shows it to the browser), and 400 if no UI can be named.
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
            _forbid_view(self, auth.caller, auth.via, name)
            return
        grants = entry.grants if entry is not None else None
        for header, value in identity_headers(
            auth, settings.get("auth_store"), name, grants
        ).items():
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
