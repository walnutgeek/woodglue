"""Runs and trigger changes record which User caused them."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any

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

ROLES = {"ops": ["call jobs", "read jobs", "trigger jobs"]}


async def shout(text: str = "hi") -> str:
    return text.upper()


class RunInitiatorTestCase(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    store: AuthStore  # pyright: ignore[reportUninitializedInstanceVariable]
    token: str  # pyright: ignore[reportUninitializedInstanceVariable]
    user_id: str  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self._tmp.name) / "auth.db", resolve_roles(ROLES))
        user = self.store.sign_in_person(
            "okta", "ann", email="ann@x.com", email_verified=True, name="Ann", role_sources={}
        )
        self.store.assign_role(user.id, "ops")
        self.user_id = user.id
        self.token = self.store.create_token(user.id, "t").secret
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self) -> tornado.web.Application:
        tmp = Path(self._tmp.name)
        ns = Namespace()
        ns.register(shout, nsref="t:shout")
        dag = Dag()
        dag.node(ns.get("t:shout"))
        ns.register(
            dag,
            nsref="pipe",
            config=NsNodeConfig(nsref="pipe", triggers=[TriggerConfig(name="hook", type="push")]),
        )
        mount = MountContext("jobs", tmp)
        storage = LythStorageConfig()
        storage.resolve_paths(mount.state_dir)
        storage.log_file = None
        ns.mount(storage)
        engine = create_engine(mount, ns)
        engine.trigger_manager.activate_configured()
        registry = EngineRegistry()
        registry.register(engine)
        namespaces: dict[str, Any] = {"jobs": (ns, NamespaceEntry(gref="unused"))}
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

    def rpc(self, method: str, **params: Any) -> Any:
        body = json.dumps({"jsonrpc": "2.0", "method": method, "params": params, "id": 1})
        resp = self.fetch(
            "/rpc", method="POST", body=body, headers={"Authorization": f"Bearer {self.token}"}
        )
        data = json.loads(resp.body)
        assert "result" in data, data
        return data["result"]


class TestRunInitiators(RunInitiatorTestCase):
    def test_fired_run_records_the_user(self) -> None:
        fired = self.rpc("system.fire_trigger", namespace="jobs", name="hook")
        [run] = [
            r
            for r in self.rpc("system.recent_runs", namespace="jobs")
            if r["run_id"] == fired["run_id"]
        ]
        assert run["initiated_by"] == self.user_id
        assert (
            self.rpc("system.inspect_run", namespace="jobs", run_id=fired["run_id"])["initiated_by"]
            == self.user_id
        )

    def test_trigger_changes_record_the_user(self) -> None:
        self.rpc("system.deactivate_trigger", namespace="jobs", name="hook")
        activation = self.rpc("system.activate_trigger", namespace="jobs", name="hook")
        assert activation["changed_by"] == self.user_id

    def test_initiators_are_shown_by_name(self) -> None:
        names = self.rpc(
            "system.describe_initiators",
            initiators=[self.user_id, "scheduler", "cli:bob", "usr_gone"],
        )
        assert names == {
            self.user_id: "Ann <ann@x.com>",
            "scheduler": "scheduler",
            "cli:bob": "cli:bob",
            "usr_gone": "usr_gone",
        }
