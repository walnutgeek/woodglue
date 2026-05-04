# Woodglue

**Self-documenting opinionated async server that hosts logic and data.**

Woodglue is a JSON-RPC 2.0 server built on Tornado and
[lythonic](https://github.com/walnutgeek/lythonic) that automatically
generates LLM-friendly documentation from your Python functions and
Pydantic models.

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

Woodglue is the HTTP layer; lythonic provides the core runtime
(namespaces, DAG orchestration, caching, and scheduled triggers).

## Features

- **JSON-RPC 2.0 with multi-namespace routing** — register Python functions, call them over HTTP with dot-prefixed namespace routing
- **Auto-generated docs** — `llms.txt` index, per-method markdown, OpenAPI 3.0.3 spec
- **Built-in UI** — browser-based API browsing, trigger management, DAG visualization
- **Bearer token authentication** — configurable per-namespace auth
- **Typed async client** — auto-resolved return types via `x-global-ref`
- **YAML-driven namespace configuration** — declarative setup with per-method tags and explicit gref/file/entries fields
- **DAG orchestration with scheduled triggers** — workflow execution and scheduling via lythonic
- **System namespace** — the server's own management API is a regular namespace (dogfooding)

## Quick Start

```python
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
ns.register(pydantic_hello, tags=["api"])
```

Configure in `data/woodglue.yaml`:

```yaml
namespaces:
  hello: "hello_ns.yaml"

docs:
  enabled: true
  openapi: true

ui:
  enabled: true
```

Run the server:

```bash
wgl start
```

## Endpoints

- `POST /rpc` — JSON-RPC 2.0 endpoint
- `GET /docs/llms.txt` — method index
- `GET /docs/methods/{name}.md` — per-method documentation
- `GET /docs/openapi.json` — OpenAPI spec
- `GET /ui/` — documentation UI
