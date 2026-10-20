"""
Signing in through Identity Providers: OpenID Connect and GitHub.

woodglue is its own OAuth client (see `docs/adr/0001`). A sign-in is the
authorization code flow with PKCE:

- `/auth/start/<provider>` records a pending sign-in (state, nonce, PKCE
  verifier, and where to return), binds it to the browser with a short-lived
  `wgl_login` cookie, and redirects to the provider.
- `/auth/callback/<provider>` checks that the returned `state` matches the
  cookie, exchanges the code, and turns the provider's answer into an
  `ExternalIdentity`. For OIDC that is the validated id_token (issuer,
  audience, expiry, nonce, signature against the provider's JWKS); for GitHub
  it is the user, verified primary email, orgs, and teams from the API.
- The person is then checked against the provider's `admit` rules, signed in
  as a local User (`AuthStore.sign_in_person`), and given a Session.

Admission and claim-to-Role mapping are re-evaluated at every sign-in.

Pending sign-ins live in memory for ten minutes, so a restart in the middle of
one only means signing in again.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import jwt
import tornado.httpclient
from typing_extensions import override

from woodglue.access import ADMIN_ROLE
from woodglue.config import AdmitRules, ProviderConfig, ProviderKind

logger = logging.getLogger(__name__)

PENDING_TTL_SECONDS = 600
_ID_TOKEN_ALGORITHMS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256"]


class SignInError(Exception):
    """The provider's answer could not be turned into a trustworthy identity."""


@dataclass(frozen=True)
class ExternalIdentity:
    """What an Identity Provider asserts about a person."""

    provider: str
    subject: str
    email: str | None
    email_verified: bool
    name: str
    groups: tuple[str, ...] = ()


def admits(rules: AdmitRules, who: ExternalIdentity) -> bool:
    """
    Whether `rules` admit `who`. Email rules only consider a verified email.

    >>> ann = ExternalIdentity("okta", "1", "ann@x.com", True, "Ann", ("ops",))
    >>> admits(AdmitRules(email_domains=["x.com"]), ann), admits(AdmitRules(groups=["ops"]), ann)
    (True, True)
    >>> eve = ExternalIdentity("okta", "2", "eve@x.com", False, "Eve")
    >>> admits(AdmitRules(email_domains=["x.com"]), eve)
    False
    """
    if rules.anyone:
        return True
    email = who.email.lower() if who.email and who.email_verified else None
    if email is not None:
        if email in (e.lower() for e in rules.emails):
            return True
        domain = email.rsplit("@", 1)[-1]
        if domain in (d.lower() for d in rules.email_domains):
            return True
    return any(g in who.groups for g in rules.groups)


def role_sources(
    provider: ProviderConfig, who: ExternalIdentity, admins: list[str]
) -> dict[str, list[str]]:
    """
    The Role assignments a sign-in sets, keyed by source: the provider's default
    and group-mapped Roles, and `admin` for configured admins.

    >>> p = ProviderConfig(id="okta", kind="oidc", issuer="https://i", client_id="c",
    ...     client_secret="s", admit=AdmitRules(anyone=True), default_roles=["viewer"],
    ...     group_roles={"ops": ["operator"]})
    >>> ann = ExternalIdentity("okta", "1", "ann@x.com", True, "Ann", ("ops",))
    >>> role_sources(p, ann, ["ANN@x.com"])
    {'provider:okta': ['operator', 'viewer'], 'admins': ['admin']}
    """
    roles = set(provider.default_roles)
    for group in who.groups:
        roles.update(provider.group_roles.get(group, []))
    is_admin = bool(who.email and who.email_verified) and (who.email or "").lower() in (
        a.lower() for a in admins
    )
    return {f"provider:{provider.id}": sorted(roles), "admins": [ADMIN_ROLE] if is_admin else []}


def _pkce_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


@dataclass
class PendingSignIn:
    provider: str
    next_url: str
    redirect_uri: str
    nonce: str = field(default_factory=lambda: secrets.token_urlsafe(16))
    verifier: str = field(default_factory=lambda: secrets.token_urlsafe(48))
    created: float = field(default_factory=time.monotonic)


class PendingSignIns:
    """In-memory pending sign-ins keyed by OAuth `state`."""

    _items: dict[str, PendingSignIn]

    def __init__(self) -> None:
        self._items = {}

    def add(self, pending: PendingSignIn) -> str:
        self._expire()
        state = secrets.token_urlsafe(24)
        self._items[state] = pending
        return state

    def pop(self, state: str) -> PendingSignIn | None:
        self._expire()
        return self._items.pop(state, None)

    def _expire(self) -> None:
        cutoff = time.monotonic() - PENDING_TTL_SECONDS
        for state in [s for s, p in self._items.items() if p.created < cutoff]:
            del self._items[state]


async def _fetch_json(
    request: tornado.httpclient.HTTPRequest,
) -> Any:
    client = tornado.httpclient.AsyncHTTPClient()
    try:
        resp = await client.fetch(request, raise_error=False)
    except OSError as exc:
        raise SignInError(f"Could not reach {request.url}: {exc}") from exc
    try:
        body = json.loads(resp.body or b"null")
    except json.JSONDecodeError:
        raise SignInError(f"{request.url} returned HTTP {resp.code}, not JSON") from None
    if resp.code >= 400:
        raise SignInError(f"{request.url} returned HTTP {resp.code}: {body}")
    return body


class ProviderClient(ABC):
    """Talks to one Identity Provider. Subclasses implement one protocol."""

    config: ProviderConfig

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config

    @abstractmethod
    async def authorize_url(self, pending: PendingSignIn, state: str) -> str: ...

    @abstractmethod
    async def complete(self, pending: PendingSignIn, code: str) -> ExternalIdentity: ...


class OidcClient(ProviderClient):
    _discovery: dict[str, Any] | None = None
    _jwks: jwt.PyJWKSet | None = None

    async def discovery(self) -> dict[str, Any]:
        if self._discovery is None:
            issuer = (self.config.issuer or "").rstrip("/")
            doc = await _fetch_json(
                tornado.httpclient.HTTPRequest(f"{issuer}/.well-known/openid-configuration")
            )
            if not isinstance(doc, dict) or "authorization_endpoint" not in doc:
                raise SignInError(f"{issuer} has no usable OpenID Connect discovery document")
            self._discovery = doc
        return self._discovery

    @override
    async def authorize_url(self, pending: PendingSignIn, state: str) -> str:
        doc = await self.discovery()
        params = {
            "response_type": "code",
            "client_id": self.config.client_id,
            "redirect_uri": pending.redirect_uri,
            "scope": " ".join(self.config.requested_scopes()),
            "state": state,
            "nonce": pending.nonce,
            "code_challenge": _pkce_challenge(pending.verifier),
            "code_challenge_method": "S256",
        }
        return f"{doc['authorization_endpoint']}?{urlencode(params)}"

    async def _signing_key(self, kid: str | None) -> jwt.PyJWK:
        doc = await self.discovery()
        for refresh in (False, True):
            if self._jwks is None or refresh:
                keys = await _fetch_json(tornado.httpclient.HTTPRequest(doc["jwks_uri"]))
                self._jwks = jwt.PyJWKSet.from_dict(keys)
            for key in self._jwks.keys:
                if kid is None or key.key_id == kid:
                    return key
        raise SignInError(f"No signing key {kid!r} in {doc['jwks_uri']}")

    @override
    async def complete(self, pending: PendingSignIn, code: str) -> ExternalIdentity:
        doc = await self.discovery()
        basic = base64.b64encode(
            f"{self.config.client_id}:{self.config.client_secret}".encode()
        ).decode()
        tokens = await _fetch_json(
            tornado.httpclient.HTTPRequest(
                doc["token_endpoint"],
                method="POST",
                headers={
                    "Authorization": f"Basic {basic}",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                body=urlencode(
                    {
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": pending.redirect_uri,
                        "code_verifier": pending.verifier,
                    }
                ),
            )
        )
        id_token = tokens.get("id_token") if isinstance(tokens, dict) else None
        if not id_token:
            raise SignInError("Token response has no id_token")
        try:
            header = jwt.get_unverified_header(id_token)
            key = await self._signing_key(header.get("kid"))
            claims: dict[str, Any] = jwt.decode(
                id_token,
                key,
                algorithms=_ID_TOKEN_ALGORITHMS,
                audience=self.config.client_id,
                issuer=doc.get("issuer", self.config.issuer),
                options={"require": ["exp", "iat", "sub"]},
            )
        except jwt.PyJWTError as exc:
            raise SignInError(f"Invalid id_token: {exc}") from exc
        if claims.get("nonce") != pending.nonce:
            raise SignInError("id_token nonce does not match this sign-in")
        groups = claims.get(self.config.groups_claim) or []
        if isinstance(groups, str):
            groups = [groups]
        return ExternalIdentity(
            provider=self.config.id,
            subject=str(claims["sub"]),
            email=claims.get("email"),
            email_verified=claims.get("email_verified") is True or self.config.trust_email,
            name=str(claims.get("name") or claims.get("email") or claims["sub"]),
            groups=tuple(str(g) for g in groups),
        )


class GitHubClient(ProviderClient):
    @override
    async def authorize_url(self, pending: PendingSignIn, state: str) -> str:
        params = {
            "client_id": self.config.client_id,
            "redirect_uri": pending.redirect_uri,
            "scope": " ".join(self.config.requested_scopes()),
            "state": state,
            "code_challenge": _pkce_challenge(pending.verifier),
            "code_challenge_method": "S256",
            "allow_signup": "false",
        }
        return f"{self.config.github_url.rstrip('/')}/login/oauth/authorize?{urlencode(params)}"

    async def _api(self, token: str, path: str) -> Any:
        return await _fetch_json(
            tornado.httpclient.HTTPRequest(
                f"{self.config.github_api_url.rstrip('/')}/{path}",
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "woodglue",
                },
            )
        )

    @override
    async def complete(self, pending: PendingSignIn, code: str) -> ExternalIdentity:
        tokens = await _fetch_json(
            tornado.httpclient.HTTPRequest(
                f"{self.config.github_url.rstrip('/')}/login/oauth/access_token",
                method="POST",
                headers={"Accept": "application/json"},
                body=urlencode(
                    {
                        "client_id": self.config.client_id,
                        "client_secret": self.config.client_secret,
                        "code": code,
                        "redirect_uri": pending.redirect_uri,
                        "code_verifier": pending.verifier,
                    }
                ),
            )
        )
        token = tokens.get("access_token") if isinstance(tokens, dict) else None
        if not token:
            raise SignInError(f"GitHub did not issue a token: {tokens}")
        user = await self._api(token, "user")
        emails = await self._api(token, "user/emails")
        primary = next((e for e in emails if e.get("primary") and e.get("verified")), None)
        groups: list[str] = []
        if self._needs_groups():
            groups.extend(o["login"] for o in await self._api(token, "user/orgs"))
            groups.extend(
                f"{t['organization']['login']}/{t['slug']}"
                for t in await self._api(token, "user/teams")
            )
        return ExternalIdentity(
            provider=self.config.id,
            subject=str(user["id"]),
            email=primary["email"] if primary else None,
            email_verified=primary is not None,
            name=str(user.get("name") or user["login"]),
            groups=tuple(groups),
        )

    def _needs_groups(self) -> bool:
        return bool(self.config.admit.groups or self.config.group_roles)


def make_clients(providers: list[ProviderConfig]) -> Mapping[str, ProviderClient]:
    clients: dict[str, ProviderClient] = {}
    for p in providers:
        clients[p.id] = GitHubClient(p) if p.kind == ProviderKind.github else OidcClient(p)
    return clients
