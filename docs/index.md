# Woodglue

**Self-documenting opinionated async server that hosts logic and data.**

Woodglue is a JSON-RPC 2.0 server built on Tornado and
[lythonic](https://github.com/walnutgeek/lythonic) that automatically
generates LLM-friendly documentation from your Python functions and
Pydantic models, signs people in, and hosts the UIs built on top of it.

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│  woodglue                                                │
│  HTTP server · JSON-RPC · Docs                           │
│  Users · Roles · Grants · Sign-in (OIDC, GitHub, proxy)  │
│  Console · project UIs (static, Streamlit)               │
├──────────────────────────────────────────────────────────┤
│  lythonic                                                │
│  Namespaces · DAGs · Caching · Triggers                  │
└──────────────────────────────────────────────────────────┘
        ▲ optional: Caddy in front for TLS and static files
```

Woodglue is the HTTP layer; lythonic provides the core runtime
(namespaces, DAG orchestration, caching, and scheduled triggers).
The design decisions behind authentication and UI hosting are recorded in
the ADRs under `docs/adr/`, and the vocabulary (User, Role, Grant, Session,
Personal Access Token, Delegated Token, UI) in `GLOSSARY.md`.

## Features

- **JSON-RPC 2.0 with multi-namespace routing** — register Python functions, call them over HTTP with dot-prefixed namespace routing
- **Auto-generated docs** — `llms.txt` index, per-method markdown, OpenAPI 3.0.3 spec, each showing only what the caller may use
- **Users, Roles, and Grants** — per-namespace authorization; Personal Access Tokens that can be narrowed and expire ([how-to](how-to/access-control.md))
- **Sign-in** — Google, Entra ID, Okta, Auth0, or any OpenID Connect provider; GitHub; or a trusted proxy such as Cloudflare Access, Google IAP, or oauth2-proxy ([how-to](how-to/identity-providers.md))
- **The Console** — browser-based API browsing, runs and triggers, DAG visualization, and your own tokens
- **Project UIs** — host your own single-page app ([how-to](how-to/static-ui.md)) or Streamlit app ([how-to](how-to/streamlit-ui.md)) next to the Console, signed in as the same User
- **Run attribution** — runs and trigger changes record which User caused them
- **`wgl login`** — get a token for a remote server through the browser
- **Optional Caddy edge** — `wgl caddy config` prints a Caddyfile ([how-to](how-to/caddy.md))
- **Typed async client** — auto-resolved return types via `x-global-ref`
- **YAML-driven namespace configuration** — declarative setup with per-method tags and explicit gref/file/entries fields
- **DAG orchestration with scheduled triggers** — workflow execution and scheduling via lythonic
- **System namespace** — the server's own management API is a regular namespace (dogfooding)

## Quick Start

```python
# hello_api.py
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel

class HelloIn(BaseModel):
    name: str
    age: int

class HelloOut(BaseModel):
    eman: str
    ega: int

def pydantic_hello(input: HelloIn) -> HelloOut:
    return HelloOut(eman=input.name[::-1], ega=-input.age)

ns = Namespace()
ns.register(pydantic_hello, nsref="pydantic_hello", tags=["api"])
```

Configure in `data/woodglue.yaml`:

```yaml
namespaces:
  hello:
    gref: "hello_api:ns"
```

Run the server and get the admin token:

```bash
wgl start
wgl token          # rotate it with: wgl token --new
```

Open `http://127.0.0.1:5321/` and sign in with the token. See
[Getting Started](getting-started.md) for calling the API, and the how-to
guides for Users, sign-in, and UIs.

## Endpoints

- `POST /rpc` — JSON-RPC 2.0 endpoint
- `GET /docs/llms.txt` — method index
- `GET /docs/methods/{name}.md` — per-method documentation
- `GET /docs/openapi.json` — OpenAPI spec
- `GET /console/` — the Console, the built-in browser UI
- `GET /ui/{name}/` — project UIs
- `GET /auth/login` — browser sign-in; `POST /auth/logout` signs out
- `GET /auth/start/{provider}`, `/auth/callback/{provider}` — Identity Provider sign-in
- `GET /auth/cli` — approval page for `wgl login`
- `GET /auth/verify` — access check for a reverse proxy's `forward_auth`
