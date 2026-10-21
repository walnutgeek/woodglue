# Control who can do what

Every request runs as a User, and what a User may do is the sum of the Grants
in their Roles. Reference: `woodglue.access`, `woodglue.auth_store`, and
`woodglue.apps.system_api`.

## Grants and Roles

A Grant is `"<action> <pattern>"`. The pattern is a glob over namespace names,
or over `ui:<name>` for UIs:

| Action    | Allows                                                          |
|-----------|-----------------------------------------------------------------|
| `call`    | calling the namespace's methods                                 |
| `read`    | seeing its runs, run inputs and outputs, and triggers           |
| `trigger` | firing, activating, and deactivating its triggers               |
| `view`    | opening a UI: `view ui:reports`, `view ui:console`, `view ui:*` |
| `admin`   | managing Users, Role assignments, and others' tokens            |

No action implies another: `call reports` does not let anyone see its runs.

Roles are named sets of Grants in `woodglue.yaml`. `admin` is built in and
holds every Grant:

```yaml
auth:
  roles:
    viewer: ["read *", "view ui:*"]
    operator: ["call *", "read *", "trigger *", "view ui:*"]
    reporter: ["call reports", "view ui:reports"]
```

A User sees only the namespaces they hold some Grant on: others are missing
from `system.list_namespaces`, `llms.txt`, and `openapi.json`, and calling
them answers `-32601 Method not found`. A visible namespace without the
needed Grant answers `-32003 Forbidden`.

## Users

- **People** are created when they first sign in through an Identity
  Provider, which can also give them Roles (`default_roles`, `group_roles`)
  and re-applies those at every sign-in. Emails listed in `auth.admins` get
  `admin`. See [Identity Providers](identity-providers.md).
- **Service Users** are for daemons and CI. They have no sign-in, only
  Personal Access Tokens.
- **`admin`** is a built-in Service User; `wgl token` prints its tokens.

```bash
wgl user add ci                 # create a Service User
wgl user assign ci reporter     # give a Role (also: unassign)
wgl user list                   # Users, kinds, Roles, state
wgl user disable ci             # also revokes all its credentials (enable undoes the disable)
```

A `<user>` is a User id, email, or unique name. Admins can do the same over
the API with `system.list_users`, `create_service_user`, `assign_role`,
`unassign_role`, `disable_user`, and `enable_user`.

## Personal Access Tokens

Scripts send a token as `Authorization: Bearer <token>`. A token can expire,
and can be narrowed to some of its User's Grants. It never exceeds them: if
the User loses a Role, the token loses it too.

```bash
wgl user issue ci nightly --days=90 --grants="call reports"   # prints the secret once
wgl user tokens ci
wgl user revoke tok_1a2b3c
```

Signed-in people manage their own tokens on the Console's Tokens page, or with
`system.create_token`, `list_tokens`, and `revoke_token`. For a remote server,
`wgl login --server=URL` gets one through the browser.

A narrowed token cannot create tokens or start a browser Session, so it cannot
mint anything broader than itself.

## UIs

Opening the Console needs `view ui:console`, and a project UI
`view ui:<name>`, unless the UI is `public`. A Process-backed UI (Streamlit,
or any server woodglue proxies) calls the API as the viewer, limited to the
UI's own `grants`. See [Host a Streamlit app](streamlit-ui.md).

## Turning auth off

`auth.enabled: false` runs every request as an anonymous User with every
Grant, for local development. `wgl start` warns if the server then listens on
anything but loopback.
