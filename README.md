# Woodglue

![CI](https://github.com/walnutgeek/woodglue/actions/workflows/ci.yml/badge.svg)
[![PyPI](https://img.shields.io/pypi/v/woodglue)](https://pypi.org/project/woodglue/)
[![Python](https://img.shields.io/pypi/pyversions/woodglue)](https://pypi.org/project/woodglue/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

JSON-RPC 2.0 server with auto-generated docs, built on [lythonic](https://github.com/walnutgeek/lythonic).

## Architecture

```
┌─────────────────────────────────────────────────┐
│  woodglue                                       │
│  HTTP server · JSON-RPC · Auth · UI · Docs      │
├─────────────────────────────────────────────────┤
│  lythonic                                       │
│  Namespaces · DAGs · Caching · Triggers         │
└─────────────────────────────────────────────────┘
```

**lythonic** provides the core runtime: namespaces for organizing methods, DAG-based orchestration, result caching, and scheduled triggers.

**woodglue** adds the serving layer: a Tornado HTTP server, JSON-RPC 2.0 dispatch, bearer token authentication, a built-in browser UI, and automatic docs generation (llms.txt, OpenAPI, per-method markdown).

## Features

- JSON-RPC 2.0 with multi-namespace routing (`billing.create_invoice`, `analytics.query`)
- Auto-generated docs: `llms.txt` index, per-method markdown, OpenAPI 3.0.3
- Built-in UI for API browsing, trigger management, and DAG visualization
- Bearer token authentication
- Typed async client with auto-resolved return types via `x-global-ref`
- YAML-driven namespace configuration with per-method tags
- DAG orchestration with scheduled triggers (via lythonic)

## Quick Start

```bash
pip install woodglue
```

Define a namespace with methods:

```python
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel

class GreetIn(BaseModel):
    name: str

class GreetOut(BaseModel):
    message: str

def greet(input: GreetIn) -> GreetOut:
    return GreetOut(message=f"Hello, {input.name}!")

ns = Namespace()
ns.register(greet, tags=["api"])
```

Start the server:

```bash
wgl start
```

Endpoints:

- `POST /rpc` -- JSON-RPC 2.0
- `GET /docs/llms.txt` -- LLM-friendly method index
- `GET /docs/openapi.json` -- OpenAPI 3.0.3 spec
- `GET /ui/` -- browser UI

## Documentation

[walnutgeek.github.io/woodglue](https://walnutgeek.github.io/woodglue/)

## Star History

[![Star History Chart](https://api.star-history.com/svg?repos=walnutgeek/woodglue&type=Date)](https://star-history.com/#walnutgeek/woodglue&Date)
