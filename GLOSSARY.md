# Woodglue

Woodglue serves Python namespaces as a JSON-RPC API with a built-in UI, scheduled
triggers, and run history.

## Language

### Identity and access

**User**:
A person or service known to a woodglue server, held in its own records. Tokens,
roles, and revocation attach to the User, not to any outside login.
_Avoid_: account, principal

**Identity Provider**:
An outside system that vouches for who someone is, such as Google, GitHub, Entra ID,
Auth0, Okta, or a trusted reverse proxy in front of woodglue.
_Avoid_: IdP (in prose), auth provider, SSO provider

**Linked Identity**:
One Identity Provider's assertion of a person (provider plus its subject id) tied to
a User. A User may have several, e.g. Google and GitHub.
_Avoid_: login, external account

**Role**:
A named set of Grants given to Users, e.g. `admin`, `operator`, `viewer`.
_Avoid_: group, permission set

**Grant**:
Permission to perform one action on the targets matching a pattern, e.g. `call` on
`reports`, or `view` on `ui:*`.
_Avoid_: permission, scope, ACL entry

**Service User**:
A User with no Linked Identity, used by daemons and CI. It can authenticate only with
Personal Access Tokens.
_Avoid_: bot, machine account, API key

**Session**:
A browser's authenticated state after a User logs in, held in a cookie that page
scripts cannot read.
_Avoid_: login, browser token

**Personal Access Token**:
A named, expirable, revocable secret owned by a User, presented as a bearer token by
the CLI and scripts.
_Avoid_: API key, auth token, the token

### Hosted interfaces

**Console**:
The built-in woodglue web interface for browsing namespaces, runs, and triggers.
_Avoid_: UI (alone), dashboard, admin UI

**UI**:
A project-specific web interface that a woodglue server hosts next to the Console,
built on the same API.
_Avoid_: app, site, frontend, SPA (as a name)

**Static UI**:
A UI made only of files that woodglue serves, such as a built single-page app.
_Avoid_: SPA UI, file UI

**Process-backed UI**:
A UI served by its own running server process, such as a Streamlit app, which
woodglue proxies to.
_Avoid_: dynamic UI, app server, Streamlit UI (as the general term)

**Delegated Token**:
A secret that woodglue hands to a process-backed UI so the UI can call the API as the
viewing User, limited to the UI's own Grants. It ends when that User's Session ends.
_Avoid_: forwarded token, impersonation token
