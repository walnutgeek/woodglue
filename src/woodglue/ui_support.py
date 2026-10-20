"""
Helpers for code running inside a Process-backed UI, such as a Streamlit app.

woodglue's proxy adds two headers to every request it forwards (see
`woodglue.apps.ui_proxy`):

- `X-Woodglue-User`: JSON describing the viewing User.
- `X-Woodglue-Token`: a Delegated Token for that User's Session, present when
  they signed in through the browser.

and starts the process with `WOODGLUE_URL`, the API's loopback address.
`current_client` combines them into a `woodglue.client.WoodglueClient` that
calls the API as the viewer, with exactly their Grants:

```
import asyncio

import streamlit as st
from woodglue.ui_support import current_client, current_user

me = current_user()
st.write(f"Hello, {me.name if me else 'stranger'}")


async def load_totals():
    return await current_client().call("reports.daily_totals", day="2026-10-01")


st.write(asyncio.run(load_totals()))
```

Streamlit runs the script without an event loop, so make the client inside the
coroutine that `asyncio.run` drives, as above.

Without `headers`, both read Streamlit's `st.context.headers`; other
frameworks pass their request headers. The headers are only trustworthy
behind woodglue, which strips client-sent copies, so the UI process must
listen on loopback only, as it does when woodglue runs it.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from woodglue.client import WoodglueClient

USER_HEADER = "X-Woodglue-User"
TOKEN_HEADER = "X-Woodglue-Token"


@dataclass(frozen=True)
class ViewingUser:
    id: str | None
    name: str
    email: str | None = None
    roles: tuple[str, ...] = field(default_factory=tuple)


def _streamlit_headers() -> Mapping[str, str]:
    import streamlit as st  # pyright: ignore[reportMissingImports]

    return st.context.headers  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


def _header(headers: Mapping[str, str], name: str) -> str | None:
    lower = name.lower()
    return next((v for k, v in headers.items() if k.lower() == lower), None)


def current_user(headers: Mapping[str, str] | None = None) -> ViewingUser | None:
    """
    The User viewing the UI, or `None` if the request did not come through
    woodglue.

    >>> current_user({"x-woodglue-user": '{"id": "u1", "name": "Ann", "roles": ["viewer"]}'})
    ViewingUser(id='u1', name='Ann', email=None, roles=('viewer',))
    >>> current_user({}) is None
    True
    """
    raw = _header(headers if headers is not None else _streamlit_headers(), USER_HEADER)
    if raw is None:
        return None
    data = json.loads(raw)
    return ViewingUser(
        id=data.get("id"),
        name=data.get("name", ""),
        email=data.get("email"),
        roles=tuple(data.get("roles", ())),
    )


def current_client(
    headers: Mapping[str, str] | None = None, base_url: str | None = None
) -> WoodglueClient:
    """
    A client calling the woodglue API as the viewing User. `base_url` defaults
    to `WOODGLUE_URL`. Without a Delegated Token (e.g. auth is disabled), the
    client sends no credential.
    """
    url = base_url or os.environ.get("WOODGLUE_URL")
    if not url:
        raise RuntimeError("WOODGLUE_URL is not set; is this process running under woodglue?")
    token = _header(headers if headers is not None else _streamlit_headers(), TOKEN_HEADER)
    return WoodglueClient(url, token=token)
