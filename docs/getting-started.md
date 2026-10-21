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
ns.register(greet, nsref="greet", tags=["api"])
```

## Configure the Server

Create `data/woodglue.yaml`, pointing at the namespace object:

```yaml
namespaces:
  myapp:
    gref: "myapp.api:ns"
```

A namespace can also be declared in its own YAML file
(`myapp: {file: myapp_ns.yaml}`) or inline (`myapp: {entries: [...]}`); see
`woodglue.config.NamespaceEntry`.

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

## Sign In to the Console

Open `http://127.0.0.1:5321/`. It sends you to the login page; paste the admin
token. The Console shows the namespaces, runs, and triggers you may see, and a
Tokens page for your own Personal Access Tokens. To let people sign in with
their own accounts, configure an Identity Provider: see
[Identity Providers](how-to/identity-providers.md).

## Give Others Access

Define Roles and give them to Users. For a script, create a Service User and
issue it a Personal Access Token:

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

See [Control who can do what](how-to/access-control.md) for Grants, Roles,
and tokens.

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

## Next Steps

- [Control who can do what](how-to/access-control.md)
- [Sign in with Google, GitHub, Entra ID, Okta, or Auth0](how-to/identity-providers.md)
- [Host a project UI](how-to/static-ui.md) or [a Streamlit app](how-to/streamlit-ui.md)
- [Run behind Caddy](how-to/caddy.md)
