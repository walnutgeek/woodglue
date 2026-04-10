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
wgl server start
```

## Call via JSON-RPC

```bash
curl -X POST http://127.0.0.1:5321/rpc \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","method":"myapp.greet","params":{"input":{"name":"World"}},"id":1}'
```

## Use the Async Client

```python
from woodglue.client import WoodglueClient

client = WoodglueClient("http://127.0.0.1:5321")
await client.load_spec(strict=True)
result = await client.call("myapp.greet", input={"name": "World"})
# result is GreetOut(message="Hello, World!")
```
