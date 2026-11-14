"""Required Actions: nodes that need a namespace-declared action, such as `write`."""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Any

import pytest
import tornado.testing
import tornado.web
from lythonic.compose.engine import StorageConfig as LythStorageConfig
from lythonic.compose.namespace import Dag, Namespace, NsNodeConfig, TriggerConfig
from typing_extensions import override

from woodglue.access import resolve_roles
from woodglue.apps.server import create_app
from woodglue.apps.system_api import build_system_namespace
from woodglue.auth_store import AuthStore
from woodglue.config import NamespaceEntry, WoodglueConfig
from woodglue.engine import EngineRegistry, create_engine
from woodglue.mount import MountContext

ROLES = {
    "reader": ["call ledger", "read ledger"],
    "wildcard": ["call *", "call ledger*", "read *"],
    "writer": ["write ledger"],
    "trigger_only": ["call ledger", "trigger ledger"],
    "operator": ["call ledger", "write ledger", "trigger ledger"],
}


def balance() -> int:
    """Current balance."""
    return 42


def add_trade(amount: int) -> int:
    """Record a trade."""
    return amount


async def settle(note: str = "eod") -> str:
    return note.upper()


def ledger_namespace() -> Namespace:
    ns = Namespace()
    ns.register(balance, nsref="balance", tags=["api"])
    ns.register(add_trade, nsref="add_trade", tags=["api"], requires="write")
    ns.register(settle, nsref="t:settle")
    dag = Dag()
    dag.node(ns.get("t:settle"))
    ns.register(
        dag,
        nsref="settle",
        config=NsNodeConfig(
            nsref="settle",
            requires="write",
            triggers=[TriggerConfig(name="settle_hook", type="push")],
        ),
    )
    return ns


class RequiredActionTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    tokens: dict[str, str]  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self._tmp.name) / "auth.db", resolve_roles(ROLES))
        self.store.ensure_admin_token()
        self.tokens = {"admin": self.store.admin_tokens()[0]}
        for role in ROLES:
            user = self.store.create_service_user(role)
            self.store.assign_role(user.id, role)
            self.tokens[role] = self.store.create_token(user.id, "t").secret
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        ns = ledger_namespace()
        mount = MountContext("ledger", Path(self._tmp.name))
        storage = LythStorageConfig()
        storage.resolve_paths(mount.state_dir)
        storage.log_file = None
        ns.mount(storage)
        engine = create_engine(mount, ns)
        engine.trigger_manager.activate_configured()
        registry = EngineRegistry()
        registry.register(engine)
        namespaces: dict[str, Any] = {"ledger": (ns, NamespaceEntry(gref="unused"))}
        namespaces["system"] = (
            build_system_namespace(namespaces, registry, self.store),
            NamespaceEntry(gref="builtin:system"),
        )
        config = WoodglueConfig.model_validate(
            {
                "namespaces": {},
                "auth": {"roles": ROLES},
                "storage": {"auth_db": str(self.store.db_path)},
            }
        )
        return create_app(
            namespaces=namespaces, config=config, engine_registry=registry, auth_store=self.store
        )

    def rpc(self, who: str, method: str, **params: Any) -> dict[str, Any]:
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
        resp = self.fetch(
            "/rpc",
            method="POST",
            body=body,
            headers={"Authorization": f"Bearer {self.tokens[who]}"},
        )
        return json.loads(resp.body)


class TestRpc(RequiredActionTestCase):
    def test_call_grants_do_not_reach_a_write_node(self) -> None:
        for who in ("reader", "wildcard", "trigger_only"):
            err = self.rpc(who, "ledger.add_trade", amount=5)["error"]
            assert err["code"] == -32003, (who, err)
            assert "'write' on 'ledger'" in err["message"]

    def test_required_action_runs_the_node(self) -> None:
        for who in ("writer", "operator", "admin"):
            assert self.rpc(who, "ledger.add_trade", amount=5)["result"] == 5, who

    def test_nodes_without_a_declaration_still_need_call(self) -> None:
        assert self.rpc("reader", "ledger.balance")["result"] == 42
        assert self.rpc("writer", "ledger.balance")["error"]["code"] == -32003


class TestTriggers(RequiredActionTestCase):
    def test_trigger_alone_cannot_fire_or_activate_a_write_node(self) -> None:
        for method in ("system.fire_trigger", "system.activate_trigger"):
            err = self.rpc("trigger_only", method, namespace="ledger", name="settle_hook")["error"]
            assert err["code"] == -32003, (method, err)
            assert "'write' on 'ledger'" in err["message"]

    def test_trigger_with_the_required_action_fires(self) -> None:
        fired = self.rpc("operator", "system.fire_trigger", namespace="ledger", name="settle_hook")
        assert fired["result"]["run_id"]
        activated = self.rpc(
            "operator", "system.activate_trigger", namespace="ledger", name="settle_hook"
        )
        assert "result" in activated, activated

    def test_deactivating_needs_only_trigger(self) -> None:
        resp = self.rpc(
            "trigger_only", "system.deactivate_trigger", namespace="ledger", name="settle_hook"
        )
        assert resp["result"]["status"] == "deactivated"


class TestListings(RequiredActionTestCase):
    def test_methods_report_their_required_action(self) -> None:
        listed = self.rpc("reader", "system.list_methods", namespace="ledger")["result"]
        assert {m["nsref"]: m["required_action"] for m in listed} == {
            "balance": "call",
            "add_trade": "write",
        }
        assert {m["nsref"]: m["can_call"] for m in listed} == {
            "balance": True,
            "add_trade": False,
        }
        writer = self.rpc("operator", "system.list_methods", namespace="ledger")["result"]
        assert all(m["can_call"] for m in writer)
        described = self.rpc(
            "reader", "system.describe_method", namespace="ledger", nsref="add_trade"
        )["result"]
        assert described["required_action"] == "write"
        assert described["can_call"] is False

    def test_docs_show_the_required_action(self) -> None:
        headers = {"Authorization": f"Bearer {self.tokens['reader']}"}
        index = self.fetch("/docs/llms.txt", headers=headers).body.decode()
        [trade] = [line for line in index.splitlines() if "ledger.add_trade" in line]
        [bal] = [line for line in index.splitlines() if "ledger.balance" in line]
        assert "requires `write`" in trade and "requires" not in bal
        page = self.fetch("/docs/methods/ledger.add_trade.md", headers=headers).body.decode()
        assert "Requires the `write` action on `ledger`." in page
        spec = json.loads(self.fetch("/docs/openapi.json", headers=headers).body)
        assert spec["paths"]["/rpc/ledger.add_trade"]["post"]["x-required-action"] == "write"
        assert spec["paths"]["/rpc/ledger.balance"]["post"]["x-required-action"] == "call"


def _app_with(
    ns: Namespace,
    roles: dict[str, list[str]],
    tmp_path: Path,
    uis: dict[str, Any] | None = None,
) -> tornado.web.Application:
    config = WoodglueConfig.model_validate(
        {
            "namespaces": {},
            "uis": uis or {},
            "auth": {"roles": roles},
            "storage": {"auth_db": str(tmp_path / "auth.db")},
        }
    )
    return create_app(namespaces={"ledger": (ns, NamespaceEntry(gref="unused"))}, config=config)


def test_reserved_required_action_fails_at_startup(tmp_path: Path) -> None:
    ns = Namespace()
    ns.register(balance, nsref="balance", tags=["api"], requires="read")
    with pytest.raises(ValueError, match="'ledger.balance'.*'read' is built in and reserved"):
        _app_with(ns, {}, tmp_path)


def test_grant_naming_an_undeclared_action_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="woodglue.apps.server"):
        _app_with(
            ledger_namespace(),
            {"stray": ["publish ledger"], "ok": ["write ledger"]},
            tmp_path,
            uis={"books": {"command": ["srv"], "grants": ["glance ui:books"]}},
        )
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("'stray'" in w and "'publish'" in w for w in warnings), warnings
    assert any("UI 'books'" in w and "'glance'" in w for w in warnings), warnings
    assert not any("'ok'" in w for w in warnings), warnings
