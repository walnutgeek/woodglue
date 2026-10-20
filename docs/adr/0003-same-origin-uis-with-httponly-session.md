# UIs are hosted on the same origin and use an HttpOnly Session cookie

Project UIs are served by the same woodglue origin as the API, under `/ui/<name>/`, and
the Console moves to `/console/`. Browsers authenticate with a server-set HttpOnly
Session cookie, so no page script, including a project UI, can read a credential.
Because the cookie is sent automatically, `/rpc` accepts only `application/json`
requests and checks `Origin` as CSRF protection.

Before a request reaches a process-backed UI, such as Streamlit, the proxy strips the
Session cookie and injects the User's identity and a Delegated Token tied to the
Session. A buggy or hostile UI process therefore never holds a credential that outlives
the Session.

## Considered Options

- UIs on separate origins with CORS, each running its own OAuth flow and holding a
  bearer token. Deferred: possible later for CDN-hosted UIs, not the default.
