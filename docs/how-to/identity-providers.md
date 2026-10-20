# Sign in with Google, GitHub, Entra ID, Okta, or Auth0

woodglue signs people in through any OpenID Connect provider, and through
GitHub. Each person becomes a local User the first time they sign in, gets
Roles from the provider's settings, and receives a browser Session. Field
reference: `woodglue.config.ProviderConfig`.

## Register woodglue with the provider

Create an OAuth "web application" client at the provider and set its redirect
(callback) URL to:

```
https://<your woodglue host>/auth/callback/<provider id>
```

Behind a proxy, set `auth.public_url` to the address browsers use, so woodglue
builds the same callback URL:

```yaml
auth:
  public_url: https://wg.example.com
```

Keep the client secret out of `woodglue.yaml` by referencing an environment
variable as `${NAME}`. `wgl start` refuses to start if the variable is unset.

## Configure providers

```yaml
auth:
  roles:
    viewer: ["read *", "view ui:*"]
    operator: ["call *", "read *", "trigger *", "view ui:*"]
  admins: [you@example.com]          # get the admin Role at sign-in
  providers:
    - id: google
      kind: oidc
      label: Google
      issuer: https://accounts.google.com
      client_id: 1234.apps.googleusercontent.com
      client_secret: ${GOOGLE_CLIENT_SECRET}
      admit:
        email_domains: [example.com]
      default_roles: [viewer]

    - id: entra
      kind: oidc
      label: Microsoft
      issuer: https://login.microsoftonline.com/<tenant id>/v2.0
      client_id: ${ENTRA_CLIENT_ID}
      client_secret: ${ENTRA_CLIENT_SECRET}
      trust_email: true                  # Entra sends no email_verified claim
      admit:
        email_domains: [example.com]
      default_roles: [viewer]

    - id: okta
      kind: oidc
      label: Okta
      issuer: https://example.okta.com    # or an authorization server URL
      client_id: ${OKTA_CLIENT_ID}
      client_secret: ${OKTA_CLIENT_SECRET}
      scopes: [openid, email, profile, groups]
      admit:
        groups: [woodglue-users]
      default_roles: [viewer]
      group_roles:
        woodglue-operators: [operator]

    - id: auth0
      kind: oidc
      label: Auth0
      issuer: https://example.us.auth0.com/
      client_id: ${AUTH0_CLIENT_ID}
      client_secret: ${AUTH0_CLIENT_SECRET}
      admit:
        emails: [contractor@gmail.com]
      default_roles: [viewer]

    - id: github
      kind: github
      label: GitHub
      client_id: ${GITHUB_CLIENT_ID}
      client_secret: ${GITHUB_CLIENT_SECRET}
      admit:
        groups: [my-org]                 # org members
      group_roles:
        my-org/platform: [operator]      # team members
```

## Admission

Every provider needs an `admit` rule; a person signs in if any rule matches.
Rules are checked at every sign-in, so someone who leaves a group is refused
from then on.

- `email_domains` and `emails` only count an email the provider says is
  verified. Entra ID never says so; set `trust_email: true` for a
  single-tenant Entra client, whose addresses your organization controls.
- `groups` matches the provider's groups claim (`groups_claim`, default
  `groups`). Okta and Entra need the groups claim enabled on the client. For
  GitHub, a group is an org login (`my-org`) or a team (`my-org/platform`).
- `anyone: true` admits everyone the provider vouches for. For Google or GitHub
  that is anyone on the internet, so pair it with Roles that grant little.

## Roles

`default_roles` and the Roles `group_roles` maps to are re-applied at every
sign-in: removing someone from `woodglue-operators` in Okta removes `operator`
at their next sign-in. Roles given with `wgl user assign` are kept.

## One person, several providers

A User found by a provider is linked to it. When someone signs in through a
second provider with the same verified email, that provider is linked to the
same User. A provider that does not verify the email gets a separate User, so
an unverified address cannot take over an account.

## Tokens still work

The login page always offers a Personal Access Token form, and scripts keep
using `Authorization: Bearer`. Signed-in people create their own tokens on the
Console's Tokens page.
