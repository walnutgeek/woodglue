# Bearer Token Authentication

## Overview

Add bearer token authentication to woodglue. Enabled by default with
zero configuration — a random token is generated on first start, printed
to console, and stored in SQLite. All endpoints are protected except
static UI files.

---

## Token Storage

### Database

SQLite at `{data}/auth.db` (from `WoodglueStorageConfig.auth_db`).

```sql
CREATE TABLE tokens (
    token TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
```

### Module: `woodglue.token_store`

```python
def ensure_token(db_path: Path) -> str | None
```
If no tokens exist, generates one via `secrets.token_urlsafe(32)`,
inserts it, and returns it. If tokens already exist, returns `None`.

```python
def get_single_token(db_path: Path) -> str | None
```
Returns the token if exactly one exists, otherwise `None`.

```python
def validate_token(db_path: Path, token: str) -> bool
```
Returns `True` if the token exists in the database.

---

## Auth Middleware

### RPC Endpoint (`/rpc`)

Checks `Authorization: Bearer <token>` header in `prepare()`. On
failure, writes a JSON-RPC error response and finishes the request:

```json
{"jsonrpc": "2.0", "error": {"code": -32000, "message": "Unauthorized"}, "id": null}
```

### Docs Endpoints (`/docs/*`)

Checks `Authorization: Bearer <token>` header OR `?token=<token>`
query parameter in `prepare()`. On failure, returns HTTP 401.

### UI Static Files (`/ui/*`)

No authentication. The UI is a static SPA that handles tokens
client-side.

### Auth Disabled

When `auth.enabled` is `False`, all auth checks are skipped. Handlers
behave as they do today.

### Implementation

Auth check logic lives in a shared function called from each handler's
`prepare()`. The `auth_db` path and `auth.enabled` flag are available
via `self.application.settings`.

---

## Config

```yaml
auth:
  enabled: true  # default true
```

```python
class AuthConfig(BaseModel):
    enabled: bool = True
```

Added to `WoodglueConfig`. The `auth_db` field already exists in
`WoodglueStorageConfig` with default resolved to `{data}/auth.db` at
startup.

---

## CLI Behavior

### `wgl start`

1. If `auth.enabled`:
   - Open/create `auth_db`
   - Call `ensure_token()` — creates a token if none exist
   - If exactly one token: print `Auth token: <token>`
   - If multiple tokens: print `Auth enabled (multiple tokens configured)`
2. Pass `auth_db` path to `create_app` via settings

### Token Auto-Discovery

When `WoodglueClient` is given a `data_dir` parameter (same machine),
it reads the token directly from `auth_db` instead of requiring it
to be passed explicitly.

---

## Client Changes

`WoodglueClient` gains a `token` parameter:

```python
client = WoodglueClient("http://localhost:5321", token="abc123")

# Or auto-discover from local auth_db
client = WoodglueClient("http://localhost:5321", data_dir=Path("./data"))
```

The token is sent as `Authorization: Bearer <token>` on all requests
(both `/rpc` calls and `/docs/openapi.json` for spec loading).

Resolution order:
1. Explicit `token` parameter
2. Auto-discovery from `data_dir` (if provided and `auth_db` exists)
3. No token (requests sent without auth header)

---

## UI Token Flow

1. UI loads as static SPA at `/ui/` (no auth)
2. On init, tries to fetch `/docs/llms.txt` with token from cookie
3. If 401 or no cookie, shows a simple "Enter token" input field
4. User pastes token from console, UI stores in cookie
5. All subsequent `/rpc` and `/docs/*` requests include the token
6. On 401 response at any point, clears cookie and shows input again

---

## Rename `auth.py` → `crypto.py`

The existing `src/woodglue/auth.py` contains Ed25519 key management
and signing primitives — not an authentication system. Rename to
`crypto.py` to avoid confusion with the new auth middleware.

---

## Files Affected

| File | Changes |
|------|---------|
| `src/woodglue/token_store.py` | New: `ensure_token`, `get_single_token`, `validate_token` |
| `src/woodglue/auth.py` → `src/woodglue/crypto.py` | Rename only |
| `src/woodglue/config.py` | Add `AuthConfig` to `WoodglueConfig` |
| `src/woodglue/cli.py` | Token init on start, pass `auth_db` to app |
| `src/woodglue/apps/rpc.py` | Auth check in `prepare()` |
| `src/woodglue/apps/llm_docs.py` | Auth check in doc handler `prepare()` methods |
| `src/woodglue/apps/server.py` | Pass `auth_db` and `auth_enabled` to app settings |
| `src/woodglue/client.py` | `token` param, `data_dir` auto-discovery |
| `src/woodglue/ui/src/main.js` | Token input UI, cookie storage, auth header on fetches |
| `src/woodglue/ui/src/style.css` | Styles for token input |
| `tests/test_token_store.py` | New: token store tests |
| `tests/test_auth_middleware.py` | New: auth middleware tests for RPC and docs |
| `tests/test_auth.py` | Update import path `auth` → `crypto` |
