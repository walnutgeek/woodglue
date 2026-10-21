"""
Browser login and logout: `/auth/login`, `/auth/logout`, and the Identity
Provider round trip at `/auth/start/<provider>` and `/auth/callback/<provider>`
(see `woodglue.apps.identity_providers`).

Every way in ends in a Session and the `wgl_session` cookie (see
`woodglue.apps.authn`). The login page offers a button per configured Identity
Provider, and always a form for a Personal Access Token, e.g. the output of
`wgl token`. A narrowed token is refused, since the Session it would start
would carry all of the User's Grants.

`/auth/cli` and `/auth/cli/token` let `wgl login` get a Personal Access Token
for the signed-in User: the browser approves on the server's page, which hands
a one-time code to the CLI's loopback address; the CLI exchanges the code,
with the PKCE verifier only it knows, for the token (see `woodglue.cli_login`).
The token itself never appears in a URL.

The login pages accept a `next` path to return to; anything that is not a path on
this server is replaced by `/`, so the login page cannot be used as an open
redirect.
"""

from __future__ import annotations

import html
import json
import logging
import secrets
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from textwrap import dedent
from urllib.parse import quote, urlencode, urlsplit

import tornado.web
from typing_extensions import override

from woodglue.access import Caller
from woodglue.apps.authn import (
    authenticate,
    clear_session_cookie,
    is_same_origin,
    public_scheme,
    set_session_cookie,
)
from woodglue.apps.identity_providers import (
    PendingSignIn,
    PendingSignIns,
    ProviderClient,
    SignInError,
    admits,
    pkce_challenge,
    role_sources,
)
from woodglue.apps.ui_proxy import UiSockets
from woodglue.auth_store import AuthStore, UserDisabled
from woodglue.config import AuthConfig

logger = logging.getLogger(__name__)

LOGIN_COOKIE = "wgl_login"


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
    """The login page HTML: provider buttons (if any) above the token form."""
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


def _providers_html(handler: tornado.web.RequestHandler, next_url: str) -> str:
    clients: Mapping[str, ProviderClient] = handler.application.settings.get("login_providers", {})
    if not clients:
        return ""
    links = "".join(
        f'<a href="/auth/start/{pid}?next={quote(next_url, safe="")}">'
        + f"Continue with {html.escape(c.config.display_name)}</a>"
        for pid, c in clients.items()
    )
    return f'<div class="providers">{links}</div><div class="divider">or</div>'


def _login_page(
    handler: tornado.web.RequestHandler, next_url: str, error: str | None = None
) -> str:
    return render_login_page(next_url, error, _providers_html(handler, next_url))


class LoginHandler(tornado.web.RequestHandler):
    """GET shows the login page; POST exchanges a token for a Session."""

    @override
    def get(self) -> None:
        self.write(_login_page(self, safe_next(self.get_argument("next", None))))

    @override
    def post(self) -> None:
        if not is_same_origin(self.request):
            raise tornado.web.HTTPError(403, "Cross-site login rejected")
        next_url = safe_next(self.get_argument("next", None))
        store: AuthStore = self.application.settings["auth_store"]
        caller = store.resolve(self.get_argument("token", "").strip())
        error = None
        if caller is None or caller.user_id is None or caller.credential_kind != "token":
            error = "That token is not valid."
        elif not caller.may_issue_credentials:
            error = "That token is narrowed; sign in with an unnarrowed token."
        if error is not None:
            self.set_status(401)
            self.write(_login_page(self, next_url, error))
            return
        assert caller is not None and caller.user_id is not None
        set_session_cookie(self, store.create_session(caller.user_id))
        self.redirect(next_url)


class LogoutHandler(tornado.web.RequestHandler):
    """POST ends the Session, closes its UI websockets, and returns to the login page."""

    @override
    def post(self) -> None:
        if not is_same_origin(self.request):
            raise tornado.web.HTTPError(403, "Cross-site logout rejected")
        auth = authenticate(self)
        store: AuthStore = self.application.settings["auth_store"]
        if auth.session_secret is not None:
            store.end_session(auth.session_secret)
            sockets: UiSockets = self.application.settings["ui_sockets"]
            sockets.close_session(auth.session_secret)
        clear_session_cookie(self)
        self.redirect("/auth/login")


def _public_base(handler: tornado.web.RequestHandler) -> str:
    auth_config: AuthConfig = handler.application.settings["config"].auth
    if auth_config.public_url:
        return auth_config.public_url.rstrip("/")
    return f"{handler.request.protocol}://{handler.request.host}"


def _client(handler: tornado.web.RequestHandler, provider_id: str) -> ProviderClient:
    clients: Mapping[str, ProviderClient] = handler.application.settings.get("login_providers", {})
    client = clients.get(provider_id)
    if client is None:
        raise tornado.web.HTTPError(404)
    return client


class StartSignInHandler(tornado.web.RequestHandler):
    """GET `/auth/start/<provider>`: send the browser to the Identity Provider."""

    @override
    async def get(self, provider_id: str) -> None:
        client = _client(self, provider_id)
        pending = PendingSignIn(
            provider=provider_id,
            next_url=safe_next(self.get_argument("next", None)),
            redirect_uri=f"{_public_base(self)}/auth/callback/{provider_id}",
        )
        pending_sign_ins: PendingSignIns = self.application.settings["pending_sign_ins"]
        state = pending_sign_ins.add(pending)
        try:
            url = await client.authorize_url(pending, state)
        except SignInError as exc:
            logger.warning("Sign-in with %s could not start: %s", provider_id, exc)
            self.set_status(502)
            self.write(
                _login_page(self, pending.next_url, f"{client.config.display_name} is unavailable.")
            )
            return
        # Ties the callback to this browser, so nobody can complete a sign-in they did not start.
        self.set_cookie(
            LOGIN_COOKIE,
            state,
            path="/auth/",
            httponly=True,
            samesite="Lax",
            secure=public_scheme(self) == "https",
            expires_days=None,
        )
        self.redirect(url)


class SignInCallbackHandler(tornado.web.RequestHandler):
    """GET `/auth/callback/<provider>`: finish a sign-in and start a Session."""

    @override
    async def get(self, provider_id: str) -> None:
        client = _client(self, provider_id)
        state = self.get_argument("state", "")
        if not state or self.get_cookie(LOGIN_COOKIE) != state:
            raise tornado.web.HTTPError(400, "Sign-in state does not match this browser")
        self.clear_cookie(LOGIN_COOKIE, path="/auth/")
        pending_sign_ins: PendingSignIns = self.application.settings["pending_sign_ins"]
        pending = pending_sign_ins.pop(state)
        if pending is None or pending.provider != provider_id:
            raise tornado.web.HTTPError(400, "Sign-in expired; start again")
        name = client.config.display_name
        if self.get_argument("error", None):
            self._fail(401, pending, f"{name} did not sign you in: {self.get_argument('error')}")
            return
        try:
            who = await client.complete(pending, self.get_argument("code", ""))
        except SignInError as exc:
            logger.warning("Sign-in with %s failed: %s", provider_id, exc)
            self._fail(401, pending, f"Sign-in with {name} failed.")
            return
        if not admits(client.config.admit, who):
            logger.info("Sign-in with %s refused for %s (%s)", provider_id, who.subject, who.email)
            self._fail(403, pending, f"Your {name} account is not allowed on this server.")
            return
        store: AuthStore = self.application.settings["auth_store"]
        auth_config: AuthConfig = self.application.settings["config"].auth
        try:
            user = store.sign_in_person(
                provider_id,
                who.subject,
                email=who.email,
                email_verified=who.email_verified,
                name=who.name,
                role_sources=role_sources(client.config, who, auth_config.admins),
            )
        except UserDisabled:
            self._fail(403, pending, "Your account is disabled.")
            return
        logger.info("Signed in %s (%s) with %s", user.id, user.email, provider_id)
        set_session_cookie(self, store.create_session(user.id))
        self.redirect(pending.next_url)

    def _fail(self, status: int, pending: PendingSignIn, error: str) -> None:
        self.set_status(status)
        self.write(_login_page(self, pending.next_url, error))


_CLI_CODE_TTL_SECONDS = 120

_CONFIRM_PAGE = dedent("""
    <!DOCTYPE html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Authorize the woodglue CLI</title>
      <style>
        :root {{ color-scheme: light dark; }}
        body {{ margin: 0; min-height: 100vh; display: grid; place-items: center;
               font: 15px/1.5 system-ui, sans-serif; }}
        main {{ width: min(420px, calc(100vw - 32px)); padding: 28px;
               border: 1px solid #8884; border-radius: 10px; }}
        button {{ margin-top: 12px; width: 100%; padding: 10px; border: 0; border-radius: 6px;
                 background: #b4632d; color: #fff; font: inherit; cursor: pointer; }}
      </style>
    </head>
    <body>
      <main>
        <h1>Authorize the woodglue CLI</h1>
        <p>Create a Personal Access Token named <b>{name}</b> for <b>{user}</b> and hand it
           to the command line on this computer?</p>
        <form method="post" action="/auth/cli">
          {fields}
          <button type="submit">Authorize</button>
        </form>
      </main>
    </body>
    </html>
    """).strip()


def is_loopback_redirect(uri: str) -> bool:
    """
    Whether `uri` points at a CLI listening on this computer.

    >>> is_loopback_redirect("http://127.0.0.1:53111/cb"), is_loopback_redirect("http://localhost:9/")
    (True, True)
    >>> is_loopback_redirect("http://127.0.0.1/cb"), is_loopback_redirect("https://evil.example:9/")
    (False, False)
    """
    parts = urlsplit(uri)
    return (
        parts.scheme == "http"
        and parts.hostname in ("127.0.0.1", "localhost", "::1")
        and parts.port is not None
    )


@dataclass
class _CliCode:
    user_id: str
    name: str
    challenge: str
    created: float = field(default_factory=time.monotonic)


class CliCodes:
    """One-time codes handed to a CLI's loopback address, by code."""

    _codes: dict[str, _CliCode]

    def __init__(self) -> None:
        self._codes = {}

    def add(self, user_id: str, name: str, challenge: str) -> str:
        """Issue a code for an approved request, dropping expired ones."""
        cutoff = time.monotonic() - _CLI_CODE_TTL_SECONDS
        self._codes = {c: v for c, v in self._codes.items() if v.created >= cutoff}
        code = secrets.token_urlsafe(24)
        self._codes[code] = _CliCode(user_id, name, challenge)
        return code

    def redeem(self, code: str, verifier: str) -> _CliCode | None:
        """
        The approval behind `code` if `verifier` matches its challenge. Any
        attempt uses the code up, so a wrong verifier cannot be retried.
        """
        entry = self._codes.pop(code, None)
        if entry is None or time.monotonic() - entry.created > _CLI_CODE_TTL_SECONDS:
            return None
        if not secrets.compare_digest(pkce_challenge(verifier), entry.challenge):
            return None
        return entry


class CliAuthorizeHandler(tornado.web.RequestHandler):
    """GET shows the approval page; POST issues a one-time code to the CLI's loopback address."""

    def _params(self) -> dict[str, str]:
        params = {
            k: self.get_argument(k, "") for k in ("redirect_uri", "state", "code_challenge", "name")
        }
        if not is_loopback_redirect(params["redirect_uri"]) or not params["code_challenge"]:
            raise tornado.web.HTTPError(400, "redirect_uri must be a loopback address with a port")
        params["name"] = params["name"].strip() or "wgl"
        return params

    def _caller(self) -> Caller | None:
        caller = authenticate(self).caller
        if caller is None:
            self.redirect(f"/auth/login?next={quote(self.request.uri or '/', safe='')}")
        elif caller.user_id is None:
            raise tornado.web.HTTPError(400, "Auth is disabled; the CLI needs no token")
        elif not caller.may_issue_credentials:
            raise tornado.web.HTTPError(403, "This credential cannot create tokens")
        return caller

    @override
    def get(self) -> None:
        params = self._params()
        caller = self._caller()
        if caller is None:
            return
        fields = "".join(
            f'<input type="hidden" name="{k}" value="{html.escape(v, quote=True)}">'
            for k, v in params.items()
        )
        self.write(
            _CONFIRM_PAGE.format(
                name=html.escape(params["name"]),
                user=html.escape(caller.email or caller.name),
                fields=fields,
            )
        )

    @override
    def post(self) -> None:
        if not is_same_origin(self.request):
            raise tornado.web.HTTPError(403, "Cross-site authorization rejected")
        params = self._params()
        caller = self._caller()
        if caller is None:
            return
        assert caller.user_id is not None
        codes: CliCodes = self.application.settings["cli_codes"]
        code = codes.add(caller.user_id, params["name"], params["code_challenge"])
        self.redirect(
            f"{params['redirect_uri']}?{urlencode({'code': code, 'state': params['state']})}"
        )


class CliTokenHandler(tornado.web.RequestHandler):
    """POST `{code, code_verifier}`: the Personal Access Token for an approved code."""

    @override
    def post(self) -> None:
        try:
            body = json.loads(self.request.body)
            code, verifier = str(body["code"]), str(body["code_verifier"])
        except (ValueError, KeyError, TypeError):
            raise tornado.web.HTTPError(400, "Expected JSON with code and code_verifier") from None
        codes: CliCodes = self.application.settings["cli_codes"]
        entry = codes.redeem(code, verifier)
        if entry is None:
            raise tornado.web.HTTPError(400, "Unknown, expired, or already used code")
        store: AuthStore = self.application.settings["auth_store"]
        issued = store.create_token(entry.user_id, entry.name)
        logger.info("Issued CLI token %s to %s", issued.id, entry.user_id)
        self.write({"token": issued.secret, "id": issued.id})
