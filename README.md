# Woodglue

![CI](https://github.com/walnutgeek/woodglue/actions/workflows/ci.yml/badge.svg)
[![Docs](https://github.com/walnutgeek/woodglue/actions/workflows/docs.yml/badge.svg)](https://walnutgeek.github.io/woodglue/)
[![PyPI](https://img.shields.io/pypi/v/woodglue)](https://pypi.org/project/woodglue/)
[![Python](https://img.shields.io/pypi/pyversions/woodglue)](https://pypi.org/project/woodglue/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

JSON-RPC 2.0 server with auto-generated docs, sign-in, and hosted UIs, built on
[lythonic](https://github.com/walnutgeek/lythonic).

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

**lythonic** provides the core runtime: namespaces for organizing methods, DAG-based
orchestration, result caching, and scheduled triggers.

**woodglue** adds the serving layer: a Tornado HTTP server, JSON-RPC 2.0 dispatch,
generated docs (llms.txt, OpenAPI, per-method markdown), Users with Roles and
per-namespace Grants, browser sign-in, the Console, and hosting for your own UIs.

## Features

- JSON-RPC 2.0 with multi-namespace routing (`billing.create_invoice`, `analytics.query`)
- Auto-generated docs: `llms.txt` index, per-method markdown, OpenAPI 3.0.3, each
  showing only what the caller may use
- Users, Roles, and per-namespace Grants (`call`, `read`, `trigger`, `view`, `admin`);
  Personal Access Tokens, optionally narrowed and expiring
- Sign-in with Google, Entra ID, Okta, Auth0 (any OpenID Connect provider) or GitHub,
  or behind Cloudflare Access, Google IAP, or oauth2-proxy
- The Console: browse the API, runs, and triggers, and manage your tokens
- Project UIs next to the Console: a single-page app, or a Streamlit app (or any
  web server) that woodglue runs and proxies, calling the API as the viewer
- Runs and trigger changes record which User caused them
- `wgl login` for remote servers, like `gh auth login`
- Optional Caddy edge: `wgl caddy config` prints a Caddyfile
- Typed async client with auto-resolved return types via `x-global-ref`
- YAML-driven namespace configuration; DAG orchestration with scheduled triggers
  (via lythonic)

## Quick Start

```bash
pip install woodglue
```

Define a namespace with methods:

```python
# myapp/api.py
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel

class GreetIn(BaseModel):
    name: str

class GreetOut(BaseModel):
    message: str

def greet(input: GreetIn) -> GreetOut:
    return GreetOut(message=f"Hello, {input.name}!")

ns = Namespace()
ns.register(greet, nsref="greet", tags=["api"])
```

Point `data/woodglue.yaml` at it and start the server:

```yaml
namespaces:
  myapp:
    gref: "myapp.api:ns"
```

```bash
wgl start
```

Auth is on by default. The server keeps Users, Roles, and tokens in
`data/auth.db`, and on first start creates a token for the built-in `admin`
User. `wgl start` never prints it:

```bash
wgl token                      # print the admin token
wgl token --new                # rotate it
```

Open `http://127.0.0.1:5321/` and sign in with that token, or configure an
Identity Provider so people sign in with their own accounts. `wgl user` manages
Users, their Roles, and their Personal Access Tokens.

Endpoints:

- `POST /rpc` -- JSON-RPC 2.0
- `GET /docs/llms.txt` -- LLM-friendly method index
- `GET /docs/openapi.json` -- OpenAPI 3.0.3 spec
- `GET /console/` -- the Console
- `GET /ui/<name>/` -- your project UIs
- `GET /auth/login` -- browser sign-in

## Documentation

[walnutgeek.github.io/woodglue](https://walnutgeek.github.io/woodglue/), including how-to
guides for access control, Identity Providers, project UIs, Streamlit, and Caddy.
