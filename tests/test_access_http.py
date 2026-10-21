"""Authorization over HTTP: `/rpc` and `/docs/*` with Users, Roles, and Grants."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

import tornado.testing
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.apps.system_api import build_system_namespace
from woodglue.auth_store import AuthStore
from woodglue.config import AuthConfig, NamespaceEntry, WoodglueConfig, WoodglueStorageConfig
from woodglue.hello import hello

ROLES = {"operator": ["call reports", "read reports"], "viewer": ["read *"]}


def _namespaces() -> dict[str, tuple[Namespace, NamespaceEntry]]:
    result: dict[str, tuple[Namespace, NamespaceEntry]] = {}
    for prefix in ("reports", "billing"):
        ns = Namespace()
        ns.register(hello, nsref="hello", tags=["api"])
        result[prefix] = (ns, NamespaceEntry(gref="unused"))
    return result


class AccessTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    tokens: dict[str, str]  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self._tmp.name) / "auth.db", resolve_roles(ROLES))
        self.store.ensure_admin_token()
        self.tokens = {"admin": self.store.admin_tokens()[0]}
        for role in ("operator", "viewer"):
            user = self.store.create_service_user(role)
            self.store.assign_role(user.id, role)
            self.tokens[role] = self.store.create_token(user.id, "t").secret
        nobody = self.store.create_service_user("nobody")
        self.tokens["nobody"] = self.store.create_token(nobody.id, "t").secret
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        config = WoodglueConfig(
            namespaces={},
            auth=AuthConfig(enabled=True, roles=ROLES),
            storage=WoodglueStorageConfig(auth_db=self.store.db_path),
        )
        namespaces = _namespaces()
        namespaces["system"] = (
            build_system_namespace(namespaces, None, self.store),
            NamespaceEntry(gref="builtin:system"),
        )
        return create_app(namespaces=namespaces, config=config, auth_store=self.store)

    def rpc(self, who: str | None, method: str, **params: Any) -> dict[str, Any]:
        headers = {"Content-Type": "application/json"}
        if who is not None:
            headers["Authorization"] = f"Bearer {self.tokens.get(who, who)}"
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
        resp = self.fetch("/rpc", method="POST", body=body, headers=headers)
        return json.loads(resp.body)

    def docs(self, who: str, path: str) -> tornado.httpclient.HTTPResponse:
        return self.fetch(path, headers={"Authorization": f"Bearer {self.tokens[who]}"})


class TestRpcAccess(AccessTestCase):
    def test_call_needs_call_grant(self) -> None:
        assert self.rpc("operator", "reports.hello", name="abc")["result"] == 3
        assert self.rpc("admin", "billing.hello", name="ab")["result"] == 2

    def test_invisible_namespace_looks_missing(self) -> None:
        err = self.rpc("operator", "billing.hello", name="x")["error"]
        assert err["code"] == -32601

    def test_visible_namespace_without_call_is_forbidden(self) -> None:
        err = self.rpc("viewer", "reports.hello", name="x")["error"]
        assert err["code"] == -32003
        assert "call" in err["message"]

    def test_unauthenticated_and_unknown_tokens(self) -> None:
        assert self.rpc(None, "reports.hello", name="x")["error"]["code"] == -32000
        assert self.rpc("bogus", "reports.hello", name="x")["error"]["code"] == -32000

    def test_list_namespaces_is_filtered(self) -> None:
        def prefixes(who: str) -> list[str]:
            return [n["prefix"] for n in self.rpc(who, "system.list_namespaces")["result"]]

        assert prefixes("operator") == ["reports", "system"]
        assert prefixes("viewer") == ["billing", "reports", "system"]
        assert prefixes("nobody") == ["system"]

    def test_list_methods_of_invisible_namespace_is_not_found(self) -> None:
        err = self.rpc("operator", "system.list_methods", namespace="billing")["error"]
        assert err["code"] == -32602
        assert self.rpc("operator", "system.list_methods", namespace="reports")["result"]

    def test_system_methods_check_their_own_action(self) -> None:
        err = self.rpc("operator", "system.fire_trigger", namespace="reports", name="t")["error"]
        assert err["code"] == -32003 and "trigger" in err["message"]
        # Allowed through to the engine lookup, which fails because none is configured.
        err = self.rpc("admin", "system.fire_trigger", namespace="reports", name="t")["error"]
        assert err["code"] == -32602 and "engine" in err["message"]
        err = self.rpc("operator", "system.recent_runs", namespace="reports")["error"]
        assert "engine" in err["message"]
        err = self.rpc("operator", "system.recent_runs", namespace="billing")["error"]
        assert err["code"] == -32003

    def test_whoami(self) -> None:
        me = self.rpc("operator", "system.whoami")["result"]
        assert me["name"] == "operator" and me["roles"] == ["operator"]

    def test_users_manage_their_own_tokens(self) -> None:
        issued = self.rpc("operator", "system.create_token", name="ci", grants=["call reports"])
        secret = issued["result"]["secret"]
        assert self.rpc(secret, "reports.hello", name="ab")["result"] == 2
        names = [t["name"] for t in self.rpc("operator", "system.list_tokens")["result"]]
        assert names == ["t", "ci"]
        token_id = issued["result"]["id"]
        err = self.rpc("viewer", "system.revoke_token", token_id=token_id)["error"]
        assert err["code"] == -32602
        assert self.rpc("operator", "system.revoke_token", token_id=token_id)["result"] is True
        assert self.rpc(secret, "reports.hello", name="ab")["error"]["code"] == -32000

    def test_user_management_needs_admin(self) -> None:
        assert self.rpc("operator", "system.list_users")["error"]["code"] == -32003
        users = self.rpc("admin", "system.list_users")["result"]
        assert {u["name"] for u in users} >= {"admin", "operator", "viewer"}
        viewer_id = next(u["id"] for u in users if u["name"] == "viewer")
        assert self.rpc("admin", "system.disable_user", user=viewer_id)["result"] is None
        assert self.rpc("viewer", "system.whoami")["error"]["code"] == -32000


class TestDocsAccess(AccessTestCase):
    def test_llms_txt_lists_only_visible_namespaces(self) -> None:
        text = self.docs("operator", "/docs/llms.txt").body.decode()
        assert "reports.hello" in text
        assert "billing.hello" not in text

    def test_method_doc_of_invisible_namespace_is_404(self) -> None:
        assert self.docs("operator", "/docs/methods/reports.hello.md").code == 200
        assert self.docs("operator", "/docs/methods/billing.hello.md").code == 404

    def test_openapi_lists_only_visible_namespaces(self) -> None:
        paths = json.loads(self.docs("operator", "/docs/openapi.json").body)["paths"]
        assert "/rpc/reports.hello" in paths
        assert "/rpc/billing.hello" not in paths

    def test_query_token_is_not_accepted(self) -> None:
        resp = self.fetch(f"/docs/llms.txt?token={self.tokens['admin']}")
        assert resp.code == 401


class TestAuthDisabled(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self) -> tornado.web.Application:
        config = WoodglueConfig(namespaces={}, auth=AuthConfig(enabled=False))
        namespaces = _namespaces()
        namespaces["system"] = (
            build_system_namespace(namespaces, None, None),
            NamespaceEntry(gref="builtin:system"),
        )
        return create_app(namespaces=namespaces, config=config)

    def test_everything_is_allowed_as_anonymous(self) -> None:
        body = json.dumps({"jsonrpc": "2.0", "method": "system.whoami", "id": 1})
        me = json.loads(self.fetch("/rpc", method="POST", body=body).body)["result"]
        assert me["name"] == "anonymous"
        body = json.dumps(
            {"jsonrpc": "2.0", "method": "billing.hello", "params": {"name": "x"}, "id": 1}
        )
        assert json.loads(self.fetch("/rpc", method="POST", body=body).body)["result"] == 1
