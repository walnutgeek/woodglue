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
  `admin`. A provider with `admit_as: pending` creates them `pending`
  instead (see below). See [Identity Providers](identity-providers.md).
- **Service Users** are for daemons and CI. They have no sign-in, only
  Personal Access Tokens.
- **`admin`** is a built-in Service User; `wgl token` prints its tokens.

```bash
wgl user add ci                 # create a Service User
wgl user assign ci reporter     # give a Role (also: unassign)
wgl user list                   # Users, kinds, statuses, Roles
wgl user disable ci             # also revokes all its credentials (enable undoes the disable)
wgl user delete ci              # erases the User and revokes all its credentials
```

A `<user>` is a User id, email, or unique name. Admins can do the same over
the API with `system.list_users`, `create_service_user`, `approve_user`,
`assign_role`, `unassign_role`, `disable_user`, `enable_user`, and
`delete_user`.

### Approving new people

Every User has a status: `pending`, `active`, or `disabled`. With
`admit_as: pending` on a provider, a person that provider admits for the first
time is created `pending`. They are signed in and their provider Roles are
recorded, but they hold no Grants, so every namespace, UI, and doc page is
closed to them, and they cannot create Personal Access Tokens. `system.whoami`
and signing out still work. People listed in `auth.admins` are always created
`active`, so the first admin of a fresh deployment is never left waiting, and a
`pending` User added to `auth.admins` later becomes `active` at their next
sign-in. A disabled admin stays disabled.

Each new pending User is logged at INFO as `New pending User usr_… via <provider>`.

```bash
wgl user list                            # shows each User's status
wgl user approve pat@example.com         # pending -> active, with the provider's Roles
wgl user approve pat@example.com --role=operator,viewer   # and these Roles too
wgl user disable pat@example.com         # deny: the record is kept, so they stay refused
```

Approval takes effect on their next request, with no new sign-in. Approving
someone who is not `pending` fails, so acting on a stale list does not
silently do nothing. `enable` turns a disabled User `active`, never back into
`pending`; a pending User is approved, not enabled. `admit_as` applies only
when a sign-in creates the User: an existing User keeps their status when
signing in through another provider.

`disable` keeps the record, so the person stays refused. `delete` is for erasure
on request: it removes the User's name, email, Role assignments, Linked
Identities, and credentials from `auth.db`, at once and with no undo, and takes
no confirmation. The built-in `admin` cannot be deleted. As with `disable`,
websockets to Process-backed UIs opened under a browser Session close within 30
seconds; other open connections run until they close.

A deleted person who signs in again becomes a new User, if the Identity
Provider's `admit` rules let them in. Behind a trusted proxy that happens on
their next request, so remove them at the proxy as well.

Run history is not touched: `initiated_by` and `changed_by` keep the deleted
User's id, which the Console then shows in place of a name. Server logs keep
anything already written. Sign-in log lines name the User by id only, but logs
from versions before User status included the email at each sign-in.

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
mint anything broader than itself. Pending and disabled Users get no tokens by
any route, including a `wgl login` approved just before the status changed.

## UIs

Opening the Console needs `view ui:console`, and a project UI
`view ui:<name>`, unless the UI is `public`. A Process-backed UI (Streamlit,
or any server woodglue proxies) calls the API as the viewer, limited to the
UI's own `grants`. See [Host a Streamlit app](streamlit-ui.md).

## Turning auth off

`auth.enabled: false` runs every request as an anonymous User with every
Grant, for local development. `wgl start` warns if the server then listens on
anything but loopback.
