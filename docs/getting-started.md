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

Auth is enabled by default (`auth.enabled` in `woodglue.yaml`). The server
keeps Users, Role assignments, and tokens in `data/auth.db`. On first start it
creates a token for the built-in `admin` User, which holds every Grant.
`wgl start` never prints it. To see or rotate it:

```bash
wgl token          # print the admin token
wgl token --new    # replace it; the running server picks it up immediately
```

To give a script narrower access, define a Role and issue a Personal Access
Token to a Service User:

```yaml
auth:
  roles:
    reporter: ["call myapp", "read myapp"]
```

```bash
wgl user add ci
wgl user assign ci reporter
wgl user issue ci nightly --days=90       # prints the secret once
```

A Grant is `"<action> <pattern>"`, where the action is `call`, `read`,
`trigger`, `view`, or `admin`, and the pattern is a glob over namespace names.
See `woodglue.access` for details.

For a remote server, sign in through the browser instead; the token is
stored for that server and used by `WoodglueClient`:

```bash
wgl login --server=https://wg.example.com
wgl logout --server=https://wg.example.com    # revoke it
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
