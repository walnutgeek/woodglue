"""
Browser login and logout: `/auth/login` and `/auth/logout`.

Login exchanges a credential for a Session and sets the `wgl_session` cookie
(see `woodglue.apps.authn`). With no Identity Provider configured, the
credential is a Personal Access Token pasted into the form, e.g. the output of
`wgl token`. A narrowed token is refused, since the Session it would start
would carry all of the User's Grants.

Both pages accept a `next` path to return to; anything that is not a path on
this server is replaced by `/`, so the login page cannot be used as an open
redirect.
"""

from __future__ import annotations

import html
from textwrap import dedent

import tornado.web
from typing_extensions import override

from woodglue.apps.authn import (
    authenticate,
    clear_session_cookie,
    is_same_origin,
    set_session_cookie,
)
from woodglue.auth_store import AuthStore


def safe_next(target: str | None) -> str:
    """
    A local path to redirect to after login, or `/`.

    >>> safe_next("/console/#runs"), safe_next("//evil.example"), safe_next("https://x/")
    ('/console/#runs', '/', '/')
    >>> safe_next("/\\\\evil.example"), safe_next(None)
    ('/', '/')
    """
    if not target or not target.startswith("/") or target[1:2] in ("/", "\\"):
        return "/"
    return target


_PAGE = dedent("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Sign in to woodglue</title>
      <style>
        :root {{ color-scheme: light dark; --bg: #f6f6f4; --fg: #1d1d1b; --muted: #6b6b66;
                --card: #ffffff; --border: #dcdcd6; --accent: #b4632d; --error: #b3261e; }}
        @media (prefers-color-scheme: dark) {{
          :root {{ --bg: #141413; --fg: #ecece8; --muted: #9a9a93; --card: #1e1e1c;
                  --border: #34342f; --accent: #e08a4f; --error: #f2b8b5; }}
        }}
        body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
               background: var(--bg); color: var(--fg); font: 15px/1.5 system-ui, sans-serif; }}
        main {{ width: min(420px, calc(100vw - 32px)); background: var(--card);
               border: 1px solid var(--border); border-radius: 10px; padding: 28px; }}
        h1 {{ font-size: 20px; margin: 0 0 4px; }}
        p {{ color: var(--muted); margin: 0 0 20px; }}
        label {{ display: block; font-size: 13px; margin-bottom: 6px; }}
        input {{ box-sizing: border-box; width: 100%; padding: 10px 12px; font: 13px monospace;
                border: 1px solid var(--border); border-radius: 6px; background: var(--bg);
                color: var(--fg); }}
        button {{ margin-top: 16px; width: 100%; padding: 10px; border: 0; border-radius: 6px;
                 background: var(--accent); color: #fff; font: inherit; cursor: pointer; }}
        .error {{ color: var(--error); margin: 0 0 16px; }}
        .providers {{ display: grid; gap: 8px; margin-bottom: 20px; }}
        .providers a {{ display: block; text-align: center; padding: 10px; border-radius: 6px;
                       border: 1px solid var(--border); color: var(--fg); text-decoration: none; }}
        .divider {{ text-align: center; color: var(--muted); font-size: 12px; margin: 4px 0 16px; }}
      </style>
    </head>
    <body>
      <main>
        <h1>Sign in to woodglue</h1>
        <p>{intro}</p>
        {error}
        {providers}
        <form method="post" action="/auth/login">
          <input type="hidden" name="next" value="{next}">
          <label for="token">Personal Access Token</label>
          <input id="token" name="token" type="password" autocomplete="off" autofocus>
          <button type="submit">Sign in</button>
        </form>
      </main>
    </body>
    </html>
    """).strip()


def render_login_page(next_url: str, error: str | None = None, providers_html: str = "") -> str:
    intro = (
        "Choose how to sign in."
        if providers_html
        else "Paste a Personal Access Token, e.g. the output of <code>wgl token</code>."
    )
    return _PAGE.format(
        intro=intro,
        error=f'<p class="error">{html.escape(error)}</p>' if error else "",
        providers=providers_html,
        next=html.escape(next_url, quote=True),
    )


class LoginHandler(tornado.web.RequestHandler):
    """GET shows the login form; POST exchanges a token for a Session."""

    @override
    def get(self) -> None:
        self.write(render_login_page(safe_next(self.get_argument("next", None))))

    @override
    def post(self) -> None:
        if not is_same_origin(self.request):
            raise tornado.web.HTTPError(403, "Cross-site login rejected")
        next_url = safe_next(self.get_argument("next", None))
        store: AuthStore = self.application.settings["auth_store"]
        caller = store.resolve(self.get_argument("token", "").strip())
        error = None
        if caller is None or caller.user_id is None:
            error = "That token is not valid."
        elif len(caller.grant_sets) > 1:
            error = "That token is narrowed; sign in with an unnarrowed token."
        if error is not None:
            self.set_status(401)
            self.write(render_login_page(next_url, error))
            return
        assert caller is not None and caller.user_id is not None
        set_session_cookie(self, store.create_session(caller.user_id))
        self.redirect(next_url)


class LogoutHandler(tornado.web.RequestHandler):
    """POST ends the Session and returns to the login page."""

    @override
    def post(self) -> None:
        if not is_same_origin(self.request):
            raise tornado.web.HTTPError(403, "Cross-site logout rejected")
        auth = authenticate(self)
        store: AuthStore = self.application.settings["auth_store"]
        if auth.session_secret is not None:
            store.end_session(auth.session_secret)
        clear_session_cookie(self)
        self.redirect("/auth/login")
