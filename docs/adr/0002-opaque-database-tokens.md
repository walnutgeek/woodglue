# Opaque, database-checked tokens

Sessions, Personal Access Tokens, and Delegated Tokens are random opaque strings that
are looked up in `auth.db` on every request. Revocation and Role changes take effect
immediately, which a single server gets for the cost of one SQLite lookup.

The unused Ed25519 signed-grant model in `woodglue.crypto` is removed. Signed tokens
only pay off when many servers must verify tokens without a shared store, and they
would need a denylist to support revocation.

This assumes a single woodglue server. Several servers sharing one `auth.db` is not
supported.
