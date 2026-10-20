"""Identity asserted by a trusted reverse proxy: signed JWT headers and plain headers."""

from __future__ import annotations

import json
import tempfile
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any

import jwt
import pytest
import tornado.httpclient
import tornado.testing
import tornado.web
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.apps.system_api import build_system_namespace
from woodglue.auth_store import AuthStore
from woodglue.config import AuthConfig, NamespaceEntry, WoodglueConfig, WoodglueStorageConfig

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ROLES = {"viewer": ["read *"], "ops": ["call *"]}


def _session_cookie(resp: tornado.httpclient.HTTPResponse) -> str | None:
    for header in resp.headers.get_list("Set-Cookie"):
        morsel = SimpleCookie(header).get("wgl_session")
        if morsel is not None:
            return morsel.value
    return None


class ProxyTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    trusted_sources: list[str] = ["127.0.0.0/8"]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        jwk = RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
        jwks = {"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]}
        (Path(self._tmp.name) / "jwks.json").write_text(json.dumps(jwks))
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        tmp = Path(self._tmp.name)
        auth = AuthConfig.model_validate(
            {
                "roles": ROLES,
                "providers": [
                    {
                        "id": "cf",
                        "kind": "trusted_proxy_jwt",
                        "header": "Cf-Access-Jwt-Assertion",
                        "jwks_url": (tmp / "jwks.json").as_uri(),
                        "issuer": "https://team.cloudflareaccess.com",
                        "audience": "aud-tag",
                        "admit": {"email_domains": ["x.com"]},
                        "default_roles": ["viewer"],
                    },
                    {
                        "id": "o2p",
                        "kind": "trusted_proxy_header",
                        "header": "X-Forwarded-Email",
                        "name_header": "X-Forwarded-Preferred-Username",
                        "groups_header": "X-Forwarded-Groups",
                        "trusted_sources": self.trusted_sources,
                        "admit": {"email_domains": ["x.com"]},
                        "group_roles": {"ops": ["ops"]},
                    },
                ],
            }
        )
        config = WoodglueConfig(
            namespaces={}, auth=auth, storage=WoodglueStorageConfig(auth_db=tmp / "auth.db")
        )
        self.store = AuthStore(tmp / "auth.db", auth.resolved_roles())
        namespaces: dict[str, Any] = {}
        namespaces["system"] = (
            build_system_namespace(namespaces, None, self.store),
            NamespaceEntry(gref="builtin:system"),
        )
        return create_app(namespaces=namespaces, config=config, auth_store=self.store)

    def whoami(self, headers: dict[str, str]) -> tuple[dict[str, Any], str | None]:
        body = json.dumps({"jsonrpc": "2.0", "method": "system.whoami", "id": 1})
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json", **headers},
        )
        return json.loads(resp.body), _session_cookie(resp)


def _cf_jwt(key: Any = _KEY, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://team.cloudflareaccess.com",
        "aud": ["aud-tag"],
        "sub": "cf-1",
        "email": "ann@x.com",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "k1"})


class TestTrustedProxyJwt(ProxyTestCase):
    def test_valid_assertion_signs_in_and_sets_a_session(self) -> None:
        data, session = self.whoami({"Cf-Access-Jwt-Assertion": _cf_jwt()})
        assert data["result"]["email"] == "ann@x.com"
        assert data["result"]["roles"] == ["viewer"]
        assert session is not None

    def test_session_is_reused_while_the_assertion_matches(self) -> None:
        _, session = self.whoami({"Cf-Access-Jwt-Assertion": _cf_jwt()})
        data, again = self.whoami(
            {"Cf-Access-Jwt-Assertion": _cf_jwt(), "Cookie": f"wgl_session={session}"}
        )
        assert data["result"]["email"] == "ann@x.com"
        assert again is None
        assert len(self.store.list_users()) == 2  # admin and ann

    def test_assertion_for_someone_else_replaces_the_session(self) -> None:
        _, session = self.whoami({"Cf-Access-Jwt-Assertion": _cf_jwt()})
        data, new_session = self.whoami(
            {
                "Cf-Access-Jwt-Assertion": _cf_jwt(sub="cf-2", email="bob@x.com"),
                "Cookie": f"wgl_session={session}",
            }
        )
        assert data["result"]["email"] == "bob@x.com"
        assert new_session is not None and new_session != session

    @pytest.mark.filterwarnings("ignore")
    def test_bad_assertions_are_unauthorized(self) -> None:
        for bad in (
            _cf_jwt(aud=["other"]),
            _cf_jwt(iss="https://evil.example"),
            _cf_jwt(exp=int(time.time()) - 10),
            _cf_jwt(key=_OTHER_KEY),
            "not-a-jwt",
        ):
            data, session = self.whoami({"Cf-Access-Jwt-Assertion": bad})
            assert data["error"]["code"] == -32000, bad
            assert session is None

    def test_not_admitted_is_unauthorized(self) -> None:
        data, _ = self.whoami({"Cf-Access-Jwt-Assertion": _cf_jwt(email="eve@other.org")})
        assert data["error"]["code"] == -32000


class TestTrustedProxyHeader(ProxyTestCase):
    def test_header_from_trusted_source_signs_in(self) -> None:
        data, session = self.whoami(
            {
                "X-Forwarded-Email": "cat@x.com",
                "X-Forwarded-Preferred-Username": "Cat",
                "X-Forwarded-Groups": "ops, other",
            }
        )
        assert data["result"]["name"] == "Cat"
        assert data["result"]["roles"] == ["ops"]
        assert session is not None


class TestUntrustedSource(ProxyTestCase):
    trusted_sources: list[str] = ["10.0.0.0/8"]

    def test_header_from_untrusted_source_is_ignored(self) -> None:
        data, _ = self.whoami({"X-Forwarded-Email": "cat@x.com"})
        assert data["error"]["code"] == -32000


def test_header_kind_requires_trusted_sources() -> None:
    with pytest.raises(ValueError, match="trusted_sources"):
        AuthConfig.model_validate(
            {
                "providers": [
                    {
                        "id": "p",
                        "kind": "trusted_proxy_header",
                        "header": "X-Forwarded-Email",
                        "admit": {"anyone": True},
                    }
                ]
            }
        )
