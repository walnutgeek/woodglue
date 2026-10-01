# Getting Started

## Installation

```bash
pip install woodglue
```

Or with uv:

```bash
uv add woodglue
```

## Create a Namespace

Define your methods and register them with api tags:

```python
# myapp/api.py
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel

class GreetIn(BaseModel):
    name: str

class GreetOut(BaseModel):
    message: str

def greet(input: GreetIn) -> GreetOut:
    """Greet someone by name."""
    return GreetOut(message=f"Hello, {input.name}!")

ns = Namespace()
ns.register(greet, tags=["api"])
```

## Configure the Server

Create `data/woodglue.yaml`:

```yaml
namespaces:
  myapp: "myapp_ns.yaml"
```

Create `data/myapp_ns.yaml`:

```yaml
namespace:
  - nsref: greet
    gref: "myapp.api:greet"
    tags: ["api"]
```

## Start the Server

```bash
wgl start
```

## Get the Auth Token

Bearer token auth is enabled by default (`auth.enabled` in `woodglue.yaml`).
The token is created on first start, or by `wgl token`, and stored in
`data/auth.db`. `wgl start` never prints it. To see or rotate it:

```bash
wgl token          # print the current token
wgl token --new    # replace it; the running server picks it up immediately
```

## Call via JSON-RPC

```bash
curl -X POST http://127.0.0.1:5321/rpc \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $(wgl token)" \
  -d '{"jsonrpc":"2.0","method":"myapp.greet","params":{"input":{"name":"World"}},"id":1}'
```

## Use the Async Client

```python
from pathlib import Path

from woodglue.client import WoodglueClient

client = WoodglueClient("http://127.0.0.1:5321", data_dir=Path("data"))
await client.load_spec(strict=True)
result = await client.call("myapp.greet", input={"name": "World"})
# result is GreetOut(message="Hello, World!")
```
