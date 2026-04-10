# Woodglue

**Self-documenting opinionated async server that hosts logic and data.**

Woodglue is a JSON-RPC 2.0 server built on Tornado that automatically
generates LLM-friendly documentation from your Python functions and
Pydantic models.

## Features

- **JSON-RPC 2.0** — register Python functions, call them over HTTP
- **Multi-namespace** — mount multiple namespaces with dot-prefixed routing
- **BaseModel round-trip** — Pydantic models are deserialized on input and serialized on output
- **LLM-friendly docs** — `llms.txt` index, per-method markdown, OpenAPI 3.0.3 spec
- **JS documentation UI** — built-in browser-based API docs viewer
- **Async client** — typed RPC client with auto-resolved return types via `x-global-ref`
- **YAML config** — declarative namespace configuration with per-method tags

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
wgl server start
```

Endpoints:

- `POST /rpc` — JSON-RPC 2.0 endpoint
- `GET /docs/llms.txt` — method index
- `GET /docs/methods/{name}.md` — per-method documentation
- `GET /docs/openapi.json` — OpenAPI spec
- `GET /ui/` — documentation UI
