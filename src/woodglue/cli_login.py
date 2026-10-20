"""
`wgl login` and `wgl logout`: Personal Access Tokens for remote servers.

`login` gets a token the way `gh auth login` does:

- It listens on a random port on 127.0.0.1 and opens the browser at the
  server's `/auth/cli` page (signing in first if needed), passing that
  loopback address and a PKCE challenge.
- After the person approves, the server redirects the browser to the loopback
  address with a one-time code.
- The CLI exchanges the code and its PKCE verifier for the token at
  `/auth/cli/token`, so the token never travels in a URL.

Tokens are stored per server URL in `credentials.json` under
`$WOODGLUE_CONFIG_DIR` (default `~/.config/woodglue`), readable only by you.
`woodglue.client.WoodglueClient` uses a stored token when given no other.
`logout` revokes the token on the server and forgets it.
"""

from __future__ import annotations

import json
import os
import secrets
import threading
import urllib.error
import urllib.request
import webbrowser
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

from pydantic import BaseModel
from strif import atomic_output_file
from typing_extensions import override

from woodglue.apps.identity_providers import pkce_challenge

LOGIN_TIMEOUT_SECONDS = 300


class StoredToken(BaseModel):
    token: str
    token_id: str


def credentials_path() -> Path:
    base = os.environ.get("WOODGLUE_CONFIG_DIR")
    root = Path(base) if base else Path.home() / ".config" / "woodglue"
    return root / "credentials.json"


def normalize_server(server: str) -> str:
    """
    >>> normalize_server("https://wg.example.com/")
    'https://wg.example.com'
    """
    return server.rstrip("/")


def load_credentials() -> dict[str, StoredToken]:
    path = credentials_path()
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    return {server: StoredToken.model_validate(v) for server, v in raw.items()}


def stored_token(server: str) -> str | None:
    stored = load_credentials().get(normalize_server(server))
    return stored.token if stored else None


def _save_credentials(credentials: dict[str, StoredToken]) -> None:
    path = credentials_path()
    data = json.dumps({s: t.model_dump() for s, t in credentials.items()}, indent=2)
    with atomic_output_file(path, make_parents=True, force=True) as tmp:
        # Created private, so the secret is never readable by others, even briefly.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(data)


class _Callback:
    """What the browser brought back to the loopback listener."""

    params: dict[str, str] | None = None
    done: threading.Event

    def __init__(self) -> None:
        self.done = threading.Event()


def _listener(result: _Callback) -> HTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            query = parse_qs(urlsplit(self.path).query)
            result.params = {k: v[0] for k, v in query.items()}
            body = b"<!doctype html><title>wgl</title><p>Done. You can close this tab.</p>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            result.done.set()

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    return HTTPServer(("127.0.0.1", 0), Handler)


def _open_in_browser(url: str) -> None:
    print(f"Opening {url}\nIf no browser opens, visit that address yourself.")
    webbrowser.open(url)


def login(
    server: str,
    name: str = "wgl",
    open_browser: Callable[[str], None] = _open_in_browser,
    timeout: float = LOGIN_TIMEOUT_SECONDS,
) -> StoredToken:
    """
    Get a Personal Access Token for `server` through the browser and store it.
    Raises `RuntimeError` if the browser does not come back in time or the
    server refuses.
    """
    server = normalize_server(server)
    verifier = secrets.token_urlsafe(48)
    state = secrets.token_urlsafe(16)
    result = _Callback()
    listener = _listener(result)
    port = listener.server_address[1]
    thread = threading.Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    try:
        query = urlencode(
            {
                "redirect_uri": f"http://127.0.0.1:{port}/callback",
                "state": state,
                "code_challenge": pkce_challenge(verifier),
                "name": name,
            }
        )
        open_browser(f"{server}/auth/cli?{query}")
        if not result.done.wait(timeout):
            raise RuntimeError("Timed out waiting for the browser to approve")
    finally:
        listener.shutdown()
        listener.server_close()
    params = result.params or {}
    if params.get("state") != state or "code" not in params:
        raise RuntimeError("The browser came back without a valid approval")
    request = urllib.request.Request(
        f"{server}/auth/cli/token",
        data=json.dumps({"code": params["code"], "code_verifier": verifier}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as resp:
            issued = json.load(resp)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{server} refused the code: HTTP {exc.code}") from exc
    stored = StoredToken(token=issued["token"], token_id=issued["id"])
    credentials = load_credentials()
    credentials[server] = stored
    _save_credentials(credentials)
    return stored


def logout(server: str) -> bool:
    """
    Revoke the stored token for `server` and forget it. Returns whether there
    was one. The token is forgotten even if the server cannot be reached.
    """
    server = normalize_server(server)
    credentials = load_credentials()
    stored = credentials.pop(server, None)
    if stored is None:
        return False
    request = urllib.request.Request(
        f"{server}/rpc",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "method": "system.revoke_token",
                "params": {"token_id": stored.token_id},
                "id": 1,
            }
        ).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {stored.token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30):
            pass
    except OSError:
        pass
    _save_credentials(credentials)
    return True
