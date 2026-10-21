"""
YAML-backed server configuration.

The config file lives at `{data_dir}/woodglue.yaml` and is required to run
the server. It declares storage, namespaces, documentation, UI, and engine
settings.

Logging for `wgl start` is driven by `storage.log_file`, `storage.log_level`,
`storage.loggers`, `storage.log_max_bytes`, `storage.log_backup_count`, and
the optional top-level `logging:` section; see `woodglue.log_setup`.
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Any

from lythonic.compose.engine import StorageConfig
from pydantic import BaseModel, Field, PrivateAttr, field_validator, model_validator
from pydantic_yaml import parse_yaml_file_as
from typing_extensions import override

from woodglue.access import Grant, resolve_roles

CONFIG_FILENAME = "woodglue.yaml"


class WoodglueStorageConfig(StorageConfig):
    """
    Extends lythonic StorageConfig with woodglue-specific storage and log rotation.

    `log_level` defaults to INFO here rather than lythonic's DEBUG.
    """

    auth_db: Path | None = None
    log_level: str = "INFO"
    log_max_bytes: int = 10 * 1024 * 1024
    log_backup_count: int = 5

    _log_file_explicit: bool = PrivateAttr(default=False)

    @override
    def model_post_init(self, context: Any, /) -> None:
        super().model_post_init(context)
        # Snapshot now: path resolution later assigns `log_file`, which marks it as set.
        self._log_file_explicit = "log_file" in self.model_fields_set

    @property
    def log_file_explicit(self) -> bool:
        """
        Whether `log_file` was given in the config, including an explicit `null`
        (which disables the file log).
        """
        return self._log_file_explicit


class DocsConfig(BaseModel):
    """Documentation generation settings."""

    enabled: bool = True
    openapi: bool = True


class UiConfig(BaseModel):
    """
    Web interface settings. `enabled` serves the Console at `/console/`.
    `default` names the UI that `/` redirects to: `console` or a key of `uis`.
    """

    enabled: bool = True
    default: str = "console"


class UiKind(StrEnum):
    streamlit = "streamlit"


class UiEntry(BaseModel):
    """
    A project UI served at `/ui/<name>/`, next to the Console. Exactly one of
    these says what it is:

    - `path`: a Static UI, a directory of built files (relative to the data
      dir), such as a single-page app built with `base: "/ui/<name>/"`. With
      `spa_fallback`, paths without a file extension that match no file return
      `index.html`, so client-side routes survive a reload; a missing `.js`
      file is still a 404.
    - `kind: streamlit` with `app`: a Streamlit script that woodglue runs and
      proxies to (see `woodglue.apps.ui_process`).
    - `command`: any other server that woodglue runs and proxies to. Its
      arguments may use `{port}` (a free loopback port woodglue picks) and
      `{base_path}` (`/ui/<name>`); the server must listen on that port and
      serve under that path.
    - `upstream`: the URL of a server run elsewhere, proxied to as is. It must
      serve under `/ui/<name>` too.

    A Process-backed UI (all but `path`) gets the viewing User's identity in
    request headers (see `woodglue.ui_support`), including a Delegated Token to
    call the API as them. `grants` limits that token to the overlap of the
    viewer's Grants and these, e.g. `["call reports", "read reports"]`, so a
    compromised UI process cannot use everything an admin viewer may. Without
    `grants` the token carries all of the viewer's Grants. `ready_path` is what
    woodglue polls until the process answers; `dev` turns on the framework's
    reload.

    Opening any UI needs `view` on `ui:<name>` unless `public`.
    """

    path: str | None = None
    kind: UiKind | None = None
    app: str | None = None
    command: list[str] | None = None
    upstream: str | None = None
    cwd: str | None = None
    env: dict[str, str] = {}
    ready_path: str | None = None
    dev: bool = False
    grants: list[str] | None = None
    label: str | None = None
    spa_fallback: bool = True
    public: bool = False

    @model_validator(mode="after")
    def _one_source(self) -> UiEntry:
        sources = [self.path, self.kind, self.command, self.upstream]
        if sum(s is not None for s in sources) != 1:
            raise ValueError("A UI needs exactly one of path, kind, command, or upstream")
        if self.kind == UiKind.streamlit and not self.app:
            raise ValueError("kind: streamlit needs app, the path of the Streamlit script")
        if self.grants is not None:
            if self.is_static:
                raise ValueError(
                    "grants apply to Process-backed UIs; a Static UI runs in the browser "
                    + "with the viewer's Session"
                )
            for grant in self.grants:
                Grant.parse(grant)
        return self

    @property
    def is_static(self) -> bool:
        return self.path is not None

    @property
    def is_supervised(self) -> bool:
        return self.kind is not None or self.command is not None


class NamespaceEntry(BaseModel):
    """
    Per-namespace configuration. Exactly one of `gref`, `file`, or `entries`
    must be set to specify how the namespace is instantiated.

    However it is defined, the namespace is built and called with its
    `woodglue.mount.MountContext` set as `current_mount`. A fragment needing the
    instance data dir reads `current_mount.get(None).data_dir` rather than
    taking an absolute path in its `init` config, so a copied `woodglue.yaml`
    still points at its own instance.
    """

    gref: str | None = None
    file: str | None = None
    entries: list[dict[str, Any]] | None = None
    expose_api: bool = True
    run_engine: bool = False

    @model_validator(mode="after")
    def _exactly_one_source(self) -> NamespaceEntry:
        sources = [self.gref is not None, self.file is not None, self.entries is not None]
        if sum(sources) != 1:
            raise ValueError("Exactly one of gref, file, or entries must be set")
        return self


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def expand_env(text: str, env: Mapping[str, str]) -> str:
    """
    Replace `${NAME}` references with values from `env`, so secrets stay out of
    `woodglue.yaml`. A missing variable is an error rather than an empty string.

    >>> expand_env("${ID}-x", {"ID": "abc"})
    'abc-x'
    >>> expand_env("${NOPE}", {})
    Traceback (most recent call last):
    ...
    ValueError: Environment variable NOPE is not set
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in env:
            raise ValueError(f"Environment variable {name} is not set")
        return env[name]

    return _ENV_REF.sub(replace, text)


class ProviderKind(StrEnum):
    oidc = "oidc"
    github = "github"
    trusted_proxy_jwt = "trusted_proxy_jwt"
    trusted_proxy_header = "trusted_proxy_header"

    @property
    def is_trusted_proxy(self) -> bool:
        return self in (ProviderKind.trusted_proxy_jwt, ProviderKind.trusted_proxy_header)


class AdmitRules(BaseModel):
    """
    Who an Identity Provider may sign in. A person is admitted if any rule
    matches. `groups` matches the provider's groups claim, or for GitHub an org
    login (`walnutgeek`) or team (`walnutgeek/core`). `anyone` admits every
    person the provider vouches for, which for a public provider such as Google
    or GitHub means anyone on the internet.
    """

    anyone: bool = False
    email_domains: list[str] = []
    emails: list[str] = []
    groups: list[str] = []

    def is_empty(self) -> bool:
        return not (self.anyone or self.email_domains or self.emails or self.groups)


class ProviderConfig(BaseModel):
    """
    One Identity Provider in `auth.providers`.

    - `kind: oidc` covers Google, Entra ID, Auth0, Okta, and other OpenID
      Connect providers; `issuer` is the URL its discovery document hangs off.
    - `kind: github` uses GitHub's OAuth2; `github_url` and `github_api_url`
      point at GitHub Enterprise when needed.
    - `kind: trusted_proxy_jwt` trusts a reverse proxy that signs each request's
      identity as a JWT in `header`, e.g. Cloudflare Access
      (`Cf-Access-Jwt-Assertion`), Google IAP (`x-goog-iap-jwt-assertion`), or
      oauth2-proxy. The JWT is checked against `jwks_url`, `issuer`, and
      `audience`; `email_claim` and `groups_claim` name the claims to read.
    - `kind: trusted_proxy_header` trusts a plain `header` such as
      `X-Forwarded-Email`, with optional `name_header` and `groups_header`
      (comma-separated), but only from `trusted_sources` (IPs or CIDRs).
      Anyone who reaches woodglue without the proxy can forge the header, so
      `trusted_sources` is required.

    With a trusted proxy there is no login page: the first request carrying a
    valid assertion signs the person in and starts a Session.

    `client_secret` (and any other string) may reference environment variables
    as `${NAME}`. `default_roles` and the Roles that `group_roles` maps a
    person's groups to are re-applied at every login, so removing someone from
    a group takes their Role away at their next login.
    """

    id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    kind: ProviderKind
    label: str | None = None
    issuer: str | None = None
    client_id: str | None = None
    client_secret: str | None = None
    scopes: list[str] | None = None
    groups_claim: str = "groups"
    email_claim: str = "email"
    header: str | None = None
    jwks_url: str | None = None
    audience: str | None = None
    name_header: str | None = None
    groups_header: str | None = None
    trusted_sources: list[str] = []
    trust_email: bool = False
    """
    Treat the `email` claim as verified even without `email_verified: true`.
    Only for providers whose addresses the organization controls, such as a
    single-tenant Entra ID, which does not send `email_verified`.
    """
    github_url: str = "https://github.com"
    github_api_url: str = "https://api.github.com"
    admit: AdmitRules
    default_roles: list[str] = []
    group_roles: dict[str, list[str]] = {}

    @field_validator("client_id", "client_secret", "issuer", "audience")
    @classmethod
    def _expand_env(cls, value: str | None) -> str | None:
        return expand_env(value, os.environ) if value is not None else None

    @model_validator(mode="after")
    def _check(self) -> ProviderConfig:
        required: dict[ProviderKind, list[str]] = {
            ProviderKind.oidc: ["issuer", "client_id", "client_secret"],
            ProviderKind.github: ["client_id", "client_secret"],
            ProviderKind.trusted_proxy_jwt: ["header", "jwks_url", "issuer", "audience"],
            ProviderKind.trusted_proxy_header: ["header", "trusted_sources"],
        }
        missing = [f for f in required[self.kind] if not getattr(self, f)]
        if missing:
            raise ValueError(f"Provider '{self.id}': kind {self.kind} needs {', '.join(missing)}")
        for source in self.trusted_sources:
            ipaddress.ip_network(source, strict=False)
        if self.admit.is_empty():
            raise ValueError(
                f"Provider '{self.id}': admit needs at least one rule "
                + "(email_domains, emails, groups, or anyone: true)"
            )
        return self

    @property
    def display_name(self) -> str:
        return self.label or self.id.capitalize()

    def requested_scopes(self) -> list[str]:
        if self.scopes is not None:
            return self.scopes
        if self.kind == ProviderKind.github:
            return ["read:user", "user:email", "read:org"]
        return ["openid", "email", "profile"]


class AuthConfig(BaseModel):
    """
    Authentication and authorization settings.

    `roles` maps a Role name to its Grants, written as `"<action> <pattern>"`
    strings (see `woodglue.access`). The `admin` Role is built in. When
    `enabled` is false, every request runs as an anonymous Caller with all
    Grants.

    `providers` lists the Identity Providers people can sign in with (see
    `ProviderConfig`); with none, the login page takes a Personal Access Token.
    People whose verified email is in `admins` get the `admin` Role at login.
    `public_url` is the server's address as browsers see it (e.g.
    `https://wg.example.com`), used to build login callback URLs when woodglue
    runs behind a proxy; by default it is taken from each request.
    """

    enabled: bool = True
    roles: dict[str, list[str]] = {}
    providers: list[ProviderConfig] = []
    admins: list[str] = []
    public_url: str | None = None

    @model_validator(mode="after")
    def _check(self) -> AuthConfig:
        defined = resolve_roles(self.roles)
        ids = [p.id for p in self.providers]
        if len(set(ids)) != len(ids):
            raise ValueError("auth.providers ids must be unique")
        for provider in self.providers:
            named = set(provider.default_roles)
            for roles in provider.group_roles.values():
                named.update(roles)
            unknown = sorted(named - set(defined))
            if unknown:
                raise ValueError(f"Provider '{provider.id}' names undefined Roles: {unknown}")
        return self

    def resolved_roles(self) -> dict[str, tuple[Grant, ...]]:
        return resolve_roles(self.roles)

    def provider(self, provider_id: str) -> ProviderConfig | None:
        return next((p for p in self.providers if p.id == provider_id), None)


class WoodglueConfig(BaseModel):
    """
    Root configuration loaded from `woodglue.yaml`.

    `namespaces` maps a prefix string to a `NamespaceEntry` dict with exactly
    one of `gref`, `file`, or `entries`, plus optional `expose_api` and
    `run_engine` flags.

    `uis` maps a name to a project UI served at `/ui/<name>/` (see `UiEntry`).

    `logging`, when present, is a `logging.config.dictConfig` dict that replaces
    woodglue's default logging setup (see `woodglue.log_setup`).
    """

    host: str = "127.0.0.1"
    port: int = 5321
    storage: WoodglueStorageConfig = WoodglueStorageConfig()
    namespaces: dict[str, NamespaceEntry]
    docs: DocsConfig = DocsConfig()
    ui: UiConfig = UiConfig()
    auth: AuthConfig = AuthConfig()
    uis: dict[str, UiEntry] = {}
    logging: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_uis(self) -> WoodglueConfig:
        for name in self.uis:
            if name == "console" or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name):
                raise ValueError(
                    f"UI name {name!r} must be lowercase letters, digits, '-' or '_', "
                    + "and not 'console'"
                )
        if self.ui.default != "console" and self.ui.default not in self.uis:
            raise ValueError(f"ui.default {self.ui.default!r} is not a configured UI")
        return self


def load_config(data_dir: Path) -> WoodglueConfig:
    """
    Load `WoodglueConfig` from `{data_dir}/woodglue.yaml`.

    Raises `FileNotFoundError` if the file does not exist.
    """
    config_path = data_dir / CONFIG_FILENAME
    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")
    return parse_yaml_file_as(WoodglueConfig, config_path)
