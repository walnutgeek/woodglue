# Caddy is an optional edge; woodglue owns auth

woodglue runs complete without Caddy. Tornado serves static UIs and also proxies
process-backed UIs such as Streamlit, including their websockets. When Caddy is used,
it adds TLS, multiple domains, and serving UI files at the edge, and it asks woodglue
who each request belongs to through `forward_auth`. Users, Sessions, and Grants never
live in Caddy.

## Considered Options

- OIDC inside Caddy through plugins such as caddy-security. Rejected: it needs a custom
  Caddy build, cannot enforce Grants per JSON-RPC method (the method name is inside the
  request body), and would duplicate the logic the standalone setup needs anyway.
- Require Caddy for process-backed UIs and serve only static UIs from Tornado. Rejected:
  a laptop with a Streamlit UI would need a Caddy binary, which costs more than the
  Tornado proxy (prior art: jupyter-server-proxy).
