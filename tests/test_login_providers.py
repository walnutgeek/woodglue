"""
Signing in through Identity Providers, against fake OIDC and GitHub servers
running in the same IOLoop as the woodglue app.
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import time
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

import jwt
import tornado.httpclient
import tornado.httpserver
import tornado.testing
import tornado.web
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.auth_store import AuthStore
from woodglue.config import AuthConfig, WoodglueConfig, WoodglueStorageConfig

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeProvider:
    """Shared state of the fake OIDC and GitHub servers."""

    base: str = ""
    codes: dict[str, dict[str, Any]]
    """code -> {"challenge": ..., "claims"/"github": ...}"""
    github_profiles: dict[str, dict[str, Any]]
    """access token -> {API path: response}"""

    def __init__(self) -> None:
        self.codes = {}
        self.github_profiles = {}


class _OidcDiscovery(tornado.web.RequestHandler):
    fake: FakeProvider  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def initialize(self, fake: FakeProvider) -> None:
        self.fake = fake

    @override
    def get(self) -> None:
        b = self.fake.base
        self.write(
            {
                "issuer": b,
                "authorization_endpoint": f"{b}/authorize",
                "token_endpoint": f"{b}/token",
                "jwks_uri": f"{b}/jwks",
            }
        )


class _Jwks(tornado.web.RequestHandler):
    @override
    def get(self) -> None:
        jwk = RSAAlgorithm.to_jwk(_KEY.public_key(), as_dict=True)
        self.write({"keys": [{**jwk, "kid": "k1", "alg": "RS256", "use": "sig"}]})


def _pkce_ok(entry: dict[str, Any], verifier: str) -> bool:
    digest = hashlib.sha256(verifier.encode()).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == entry["challenge"]


class _OidcToken(tornado.web.RequestHandler):
    fake: FakeProvider  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def initialize(self, fake: FakeProvider) -> None:
        self.fake = fake

    @override
    def post(self) -> None:
        auth = self.request.headers.get("Authorization", "")
        assert base64.b64decode(auth.removeprefix("Basic ")).decode() == "cid:csecret"
        entry = self.fake.codes.pop(self.get_argument("code"), None)
        if entry is None or not _pkce_ok(entry, self.get_argument("code_verifier")):
            self.set_status(400)
            self.write({"error": "invalid_grant"})
            return
        now = int(time.time())
        claims = {"iss": self.fake.base, "aud": "cid", "iat": now, "exp": now + 300}
        claims.update(entry["claims"])
        token = jwt.encode(claims, _KEY, algorithm="RS256", headers={"kid": "k1"})
        self.write({"access_token": "at", "token_type": "Bearer", "id_token": token})


class _GitHubToken(tornado.web.RequestHandler):
    fake: FakeProvider  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def initialize(self, fake: FakeProvider) -> None:
        self.fake = fake

    @override
    def post(self) -> None:
        assert self.get_argument("client_secret") == "gsecret"
        entry = self.fake.codes.pop(self.get_argument("code"), None)
        if entry is None or not _pkce_ok(entry, self.get_argument("code_verifier")):
            self.write({"error": "bad_verification_code"})
            return
        self.write({"access_token": f"gh-{entry['github']['user']['id']}", "token_type": "bearer"})


class _GitHubApi(tornado.web.RequestHandler):
    fake: FakeProvider  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def initialize(self, fake: FakeProvider) -> None:
        self.fake = fake

    @override
    def get(self, path: str) -> None:
        token = self.request.headers["Authorization"].removeprefix("Bearer ")
        profile = self.fake.github_profiles[token]
        self.write(json.dumps(profile[path]))


def _fake_app(fake: FakeProvider) -> tornado.web.Application:
    return tornado.web.Application(
        [
            (r"/\.well-known/openid-configuration", _OidcDiscovery, {"fake": fake}),
            (r"/jwks", _Jwks),
            (r"/token", _OidcToken, {"fake": fake}),
            (r"/login/oauth/access_token", _GitHubToken, {"fake": fake}),
            (r"/api/(.+)", _GitHubApi, {"fake": fake}),
        ]
    )


ROLES = {"viewer": ["read *"], "operator": ["call *"]}


def _cookie(resp: tornado.httpclient.HTTPResponse, name: str) -> str | None:
    for header in resp.headers.get_list("Set-Cookie"):
        morsel = SimpleCookie(header).get(name)
        if morsel is not None:
            return morsel.value
    return None


class LoginTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    fake: FakeProvider  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    _fake_server: tornado.httpserver.HTTPServer  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.fake = FakeProvider()
        sock, port = tornado.testing.bind_unused_port()
        self.fake.base = f"http://127.0.0.1:{port}"
        super().setUp()
        self._fake_server = tornado.httpserver.HTTPServer(_fake_app(self.fake))
        self._fake_server.add_sockets([sock])

    @override
    def tearDown(self) -> None:
        self._fake_server.stop()
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        auth = AuthConfig.model_validate(
            {
                "roles": ROLES,
                "admins": ["boss@x.com"],
                "providers": [
                    {
                        "id": "okta",
                        "kind": "oidc",
                        "label": "Okta",
                        "issuer": self.fake.base,
                        "client_id": "cid",
                        "client_secret": "csecret",
                        "admit": {"email_domains": ["x.com"], "groups": ["contractors"]},
                        "default_roles": ["viewer"],
                        "group_roles": {"ops": ["operator"]},
                    },
                    {
                        "id": "github",
                        "kind": "github",
                        "client_id": "gid",
                        "client_secret": "gsecret",
                        "github_url": self.fake.base,
                        "github_api_url": f"{self.fake.base}/api",
                        "admit": {"groups": ["walnutgeek"]},
                        "group_roles": {"walnutgeek/core": ["operator"]},
                    },
                ],
            }
        )
        config = WoodglueConfig(
            namespaces={},
            auth=auth,
            storage=WoodglueStorageConfig(auth_db=Path(self._tmp.name) / "auth.db"),
        )
        app = create_app(namespaces={}, config=config)
        self.store = app.settings["auth_store"]
        return app

    def start(self, provider: str, next_url: str = "/console/") -> tuple[dict[str, str], str]:
        """Begin a sign-in; returns the authorize URL's query and the login cookie."""
        resp = self.fetch(
            f"/auth/start/{provider}?next={next_url}", follow_redirects=False, raise_error=False
        )
        assert resp.code == 302, resp.body
        query = {k: v[0] for k, v in parse_qs(urlsplit(resp.headers["Location"]).query).items()}
        return query, f"wgl_login={_cookie(resp, 'wgl_login')}"

    def callback(
        self, provider: str, query: dict[str, str], cookie: str, code: str = "c1"
    ) -> tornado.httpclient.HTTPResponse:
        params = urlencode({"code": code, "state": query["state"]})
        return self.fetch(
            f"/auth/callback/{provider}?{params}",
            headers={"Cookie": cookie},
            follow_redirects=False,
            raise_error=False,
        )

    def oidc_sign_in(self, claims: dict[str, Any]) -> tornado.httpclient.HTTPResponse:
        query, cookie = self.start("okta")
        self.fake.codes["c1"] = {
            "challenge": query["code_challenge"],
            "claims": {"nonce": query["nonce"], **claims},
        }
        return self.callback("okta", query, cookie)

    def whoami(self, resp: tornado.httpclient.HTTPResponse) -> dict[str, Any]:
        """The signed-in User, as the Session cookie resolves."""
        session = _cookie(resp, "wgl_session")
        assert session
        caller = self.store.resolve(session)
        assert caller is not None
        return {"email": caller.email, "name": caller.name, "roles": list(caller.roles)}


class TestOidc(LoginTestCase):
    def test_login_page_lists_providers(self) -> None:
        body = self.fetch("/auth/login?next=/console/").body.decode()
        assert "/auth/start/okta?next=%2Fconsole%2F" in body
        assert "Okta" in body and "/auth/start/github" in body

    def test_authorize_redirect_uses_pkce_and_callback_url(self) -> None:
        query, _ = self.start("okta")
        assert query["code_challenge_method"] == "S256"
        assert query["client_id"] == "cid"
        assert query["redirect_uri"] == self.get_url("/auth/callback/okta")
        assert query["scope"] == "openid email profile"

    def test_admitted_person_gets_a_session_with_mapped_roles(self) -> None:
        resp = self.oidc_sign_in(
            {
                "sub": "u1",
                "email": "Ann@x.com",
                "email_verified": True,
                "name": "Ann",
                "groups": ["ops"],
            }
        )
        assert resp.code == 302 and resp.headers["Location"] == "/console/"
        me = self.whoami(resp)
        assert me["email"] == "ann@x.com"
        assert me["roles"] == ["operator", "viewer"]

    def test_admitted_by_group_without_matching_domain(self) -> None:
        resp = self.oidc_sign_in(
            {"sub": "u2", "email": "c@else.org", "email_verified": True, "groups": ["contractors"]}
        )
        assert resp.code == 302

    def test_unverified_email_does_not_count_for_admission(self) -> None:
        resp = self.oidc_sign_in({"sub": "u3", "email": "eve@x.com", "email_verified": False})
        assert resp.code == 403
        assert not _cookie(resp, "wgl_session")

    def test_configured_admins_get_admin(self) -> None:
        resp = self.oidc_sign_in({"sub": "b", "email": "boss@x.com", "email_verified": True})
        assert "admin" in self.whoami(resp)["roles"]

    def test_state_must_match_the_login_cookie(self) -> None:
        query, _ = self.start("okta")
        resp = self.callback("okta", query, "wgl_login=forged")
        assert resp.code == 400

    def test_id_token_with_wrong_nonce_is_rejected(self) -> None:
        query, cookie = self.start("okta")
        self.fake.codes["c1"] = {
            "challenge": query["code_challenge"],
            "claims": {"nonce": "other", "sub": "u1", "email": "a@x.com", "email_verified": True},
        }
        assert self.callback("okta", query, cookie).code == 401

    def test_unknown_provider_is_404(self) -> None:
        resp = self.fetch("/auth/start/nope", follow_redirects=False, raise_error=False)
        assert resp.code == 404


class TestGitHub(LoginTestCase):
    def github_sign_in(self, user_id: int, orgs: list[str], teams: list[str]) -> Any:
        token = f"gh-{user_id}"
        self.fake.github_profiles[token] = {
            "user": {"id": user_id, "login": f"user{user_id}", "name": None, "email": None},
            "user/emails": [
                {"email": f"u{user_id}@gh.example", "primary": True, "verified": True},
                {"email": "old@gh.example", "primary": False, "verified": True},
            ],
            "user/orgs": [{"login": o} for o in orgs],
            "user/teams": [
                {"slug": t.split("/")[1], "organization": {"login": t.split("/")[0]}} for t in teams
            ],
        }
        query, cookie = self.start("github")
        self.fake.codes["c1"] = {
            "challenge": query["code_challenge"],
            "github": self.fake.github_profiles[token],
        }
        return self.callback("github", query, cookie)

    def test_org_member_signs_in_with_team_roles(self) -> None:
        resp = self.github_sign_in(7, ["walnutgeek"], ["walnutgeek/core"])
        assert resp.code == 302
        me = self.whoami(resp)
        assert me["email"] == "u7@gh.example"
        assert me["name"] == "user7"
        assert me["roles"] == ["operator"]

    def test_non_member_is_refused(self) -> None:
        assert self.github_sign_in(8, ["other-org"], []).code == 403


class TestTrustEmail(LoginTestCase):
    @override
    def get_app(self) -> tornado.web.Application:
        app = super().get_app()
        app.settings["login_providers"]["okta"].config.trust_email = True
        return app

    def test_trusted_email_without_verified_claim_is_admitted(self) -> None:
        resp = self.oidc_sign_in({"sub": "e1", "email": "dana@x.com"})
        assert resp.code == 302
        assert self.whoami(resp)["email"] == "dana@x.com"
