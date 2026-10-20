# Local Users, with woodglue as its own OIDC client and optional trusted proxy

woodglue keeps its own User records and links each one to Identity Providers, instead
of treating whatever an outside login asserts as the identity. Personal Access Tokens,
Role assignments, and revocation need an owner that outlives any single login, and one
person may sign in through more than one provider.

woodglue runs the login flow itself as a generic OIDC client, which covers Google,
Entra ID, Auth0, and Okta; GitHub, which is plain OAuth2, gets a small adapter. A
trusted-proxy mode also accepts identity asserted by a reverse proxy in front of
woodglue. Both paths end in the same local User, so authorization does not depend on
how someone logged in.

## Considered Options

- Delegate all login to a reverse proxy (oauth2-proxy, Cloudflare Access) and keep no
  local Users. Rejected: the standalone Tornado setup would have no login, and tokens
  for the CLI and scripts would have no owner.
