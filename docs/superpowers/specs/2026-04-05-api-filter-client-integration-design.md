# API Tag Filtering, YAML Namespace Config, Async Client, and Integration Tests

## Overview

Five changes to make the api tag filtering consistent, add YAML-based
namespace configuration, embed type information in OpenAPI for smart
client deserialization, build an async RPC client, and tie everything
together with an integration test.

**Implementation order:** Fix api filtering → YAML namespace config →
x-global-ref in OpenAPI → Async client → Integration test.

---

## 1. Fix API Tag Filtering in llms.txt and OpenAPI

### Problem

`generate_llms_txt` and `generate_openapi_spec` take
`namespaces: dict[str, Namespace]` and iterate all methods via
`walk_namespace`. They bypass `build_method_index` and the `api` tag
filter. Only the RPC handler and `MethodDocHandler` use the filtered
`method_index`.

### Fix

Change both functions to accept `method_index: dict[str, dict[str, NamespaceNode]]`
instead of `namespaces`. They iterate the already-filtered index.

Update `LlmsTxtHandler` and `OpenApiHandler` to read
`self.application.settings["method_index"]` instead of
`self.application.settings["namespaces"]`.

After this fix, all four surfaces (RPC dispatch, llms.txt, method
markdown, OpenAPI) are driven by the same `method_index`, which only
contains api-tagged methods.

---

## 2. YAML Namespace Config Loading

### Current Behavior

`WoodglueConfig.namespaces` is `dict[str, str]` where the value is a
GlobalRef string pointing to a `Namespace` instance (e.g.,
`"woodglue.hello:ns"`).

### Extended Behavior

The value can also be a path to a YAML namespace config file (relative
to the data dir). Detection: if the value ends with `.yaml` or `.yml`,
treat it as a config file path. Otherwise treat it as a GlobalRef.

```yaml
# data/woodglue.yaml
namespaces:
  hello: "hello_ns.yaml"
  legacy: "myproject.api:ns"
```

```yaml
# data/hello_ns.yaml
entries:
  - nsref: hello
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: pydantic_hello
    gref: "woodglue.hello:pydantic_hello"
    tags: ["api"]
```

### Loading Logic

In `cli.py`, `load_namespaces` checks each value:

- If it ends with `.yaml` or `.yml`: load a `NamespaceConfig` from
  `{data_dir}/{value}` via `pydantic_yaml.parse_yaml_file_as`, then call
  `lythonic.compose.namespace_config.load_namespace(config, data_dir)`.
- Otherwise: use `GlobalRef(value).get_instance()` as before.

---

## 3. `x-global-ref` in OpenAPI Spec

When `generate_openapi_spec` encounters a BaseModel type in parameter
or return annotations, it adds an `x-global-ref` field to the JSON
Schema object:

```json
{
  "schema": {
    "title": "HelloIn",
    "type": "object",
    "properties": {"name": {"type": "string"}, "age": {"type": "integer"}},
    "required": ["name", "age"],
    "x-global-ref": "woodglue.hello:HelloIn"
  }
}
```

The GlobalRef string is derived from the BaseModel class:
`f"{cls.__module__}:{cls.__qualname__}"`.

This is a valid OpenAPI vendor extension (`x-` prefix). Standard tools
ignore it; smart clients can use it for type-safe deserialization.

---

## 4. Async RPC Client

### Module

`woodglue.client` — a new module in the main package.

### Class: `WoodglueClient`

Uses `tornado.httpclient.AsyncHTTPClient`.

```python
client = WoodglueClient("http://localhost:5321")

# Load OpenAPI spec and resolve all x-global-ref types
await client.load_spec(strict=True)   # fails if any gref unresolvable
await client.load_spec(strict=False)  # resolves what's available

# Call with auto-resolved types
result = await client.call("hello.pydantic_hello",
                           input={"name": "Alice", "age": 30})
# -> HelloOut(eman="ecilA", ega=-30)

# Explicit return type override
result = await client.call("hello.pydantic_hello",
                           input={"name": "Alice", "age": 30},
                           return_type=HelloOut)

# Custom resolver callable
def my_resolver(global_ref: str) -> type[BaseModel] | None:
    ...

result = await client.call("hello.pydantic_hello",
                           input={"name": "Alice", "age": 30},
                           resolver=my_resolver)
```

### Resolution Priority on `call()`

1. Explicit `return_type` parameter if provided
2. `resolver` callable if provided — called with the `x-global-ref`
   string, returns a BaseModel subclass or `None`
3. Type from `load_spec()` if the spec was loaded and the method has
   a resolved return type
4. Return raw dict (no deserialization)

### JSON-RPC Handling

- Builds JSON-RPC 2.0 request body with auto-incrementing `id`
- Serializes BaseModel kwargs via `model_dump(mode="json")` before
  sending
- On response: checks for `error` field, raises
  `WoodglueRpcError(code, message)` if present
- Deserializes `result` using the resolved return type if available

### Spec Loading

`load_spec()` fetches `/docs/openapi.json`, iterates all paths, and
for each method extracts `x-global-ref` from the response schema.
It tries to resolve each via `GlobalRef(ref).get_instance()`:

- `strict=True`: raises `ImportError` if any gref fails to resolve
- `strict=False`: skips unresolvable grefs (those methods return raw
  dicts)

Stores resolved types in `self._return_types: dict[str, type[BaseModel]]`
keyed by qualified method name (e.g., `"hello.pydantic_hello"`).

---

## 5. Integration Test

### Module

`tests/test_integration.py`

### Setup

Create two YAML namespace config files in a temp dir. Both reference
the same functions from `woodglue.hello` but with different nsrefs
and tags:

**`pub_ns.yaml`:**
```yaml
entries:
  - nsref: hello
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: pydantic_hello
    gref: "woodglue.hello:pydantic_hello"
    tags: ["api"]
  - nsref: secret_hello
    gref: "woodglue.hello:hello"
    # no api tag
```

**`internal_ns.yaml`:**
```yaml
entries:
  - nsref: greet
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: hidden_greet
    gref: "woodglue.hello:hello"
    # no api tag
```

A `WoodglueConfig` with `namespaces: {"pub": "pub_ns.yaml", "internal": "internal_ns.yaml"}`.

### Test Cases

**Positive (api-tagged methods appear in all four surfaces):**

- RPC: `pub.hello`, `pub.pydantic_hello`, `internal.greet` return
  successful results
- llms.txt: contains all three qualified names
- Method markdown: `/docs/methods/pub.hello.md` etc. return 200
- OpenAPI: paths exist for all three, schemas include `x-global-ref`

**Negative (non-api methods excluded from all four surfaces):**

- RPC: `pub.secret_hello` and `internal.hidden_greet` return
  METHOD_NOT_FOUND (-32601)
- llms.txt: does not contain `secret_hello` or `hidden_greet`
- Method markdown: `/docs/methods/pub.secret_hello.md` returns 404
- OpenAPI: no paths for `secret_hello` or `hidden_greet`

**Client:**

- `WoodglueClient.load_spec(strict=True)` succeeds, resolves types
- `client.call("pub.pydantic_hello", ...)` returns `HelloOut` instance
- `client.call("pub.hello", ...)` returns `int` (primitive, no
  deserialization needed)

---

## Files Affected

| File | Changes |
|------|---------|
| `src/woodglue/apps/llm_docs.py` | `generate_llms_txt` and `generate_openapi_spec` take `method_index`; handlers use `method_index`; add `x-global-ref` to BaseModel schemas |
| `src/woodglue/apps/server.py` | No changes needed (already passes `method_index`) |
| `src/woodglue/cli.py` | `load_namespaces` handles `.yaml`/`.yml` values via `NamespaceConfig` |
| `src/woodglue/client.py` | New: `WoodglueClient`, `WoodglueRpcError` |
| `tests/test_integration.py` | New: full integration test with YAML configs, all four surfaces, and client |
| `tests/test_llm_docs.py` | Update calls to `generate_llms_txt` and `generate_openapi_spec` |
| `tests/test_llm_docs_handlers.py` | Minimal changes if handler signatures change |
