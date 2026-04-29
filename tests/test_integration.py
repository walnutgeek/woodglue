"""
Integration test: verifies all four surfaces (RPC, llms.txt, markdown, OpenAPI)
agree on api-tagged method publishing. Uses YAML namespace configs.
"""

import json
import tempfile
from pathlib import Path
from typing import Any

import tornado.testing
from lythonic.compose.engine import EngineConfig
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.client import WoodglueClient, WoodglueRpcError
from woodglue.config import NamespaceEntry, WoodglueConfig
from woodglue.hello import HelloOut

PUB_NS_YAML = """\
namespace:
  - nsref: hello
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: pydantic_hello
    gref: "woodglue.hello:pydantic_hello"
    tags: ["api"]
  - nsref: secret_hello
    gref: "woodglue.hello:hello"
"""

INTERNAL_NS_YAML = """\
namespace:
  - nsref: greet
    gref: "woodglue.hello:hello"
    tags: ["api"]
  - nsref: hidden_greet
    gref: "woodglue.hello:hello"
"""


def _load_ns_from_yaml(
    yaml_content: str,
    data_dir: Path,  # pyright: ignore[reportUnusedParameter]
) -> Namespace:
    from pydantic_yaml import parse_yaml_raw_as

    engine_config = parse_yaml_raw_as(EngineConfig, yaml_content)
    ns = Namespace()
    for entry in engine_config.namespace:
        if entry.gref is not None:
            ns.register(str(entry.gref), nsref=entry.nsref, tags=entry.tags, config=entry)
    return ns


class TestIntegration(tornado.testing.AsyncHTTPTestCase):
    _tmp: tempfile.TemporaryDirectory[str]  # pyright: ignore[reportUninitializedInstanceVariable]
    _data_dir: Path  # pyright: ignore[reportUninitializedInstanceVariable]

    @override
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._data_dir = Path(self._tmp.name)
        super().setUp()

    @override
    def tearDown(self) -> None:
        super().tearDown()
        self._tmp.cleanup()

    @override
    def get_app(self):
        namespaces = {
            "pub": (
                _load_ns_from_yaml(PUB_NS_YAML, self._data_dir),
                NamespaceEntry(file="pub_ns.yaml"),
            ),
            "internal": (
                _load_ns_from_yaml(INTERNAL_NS_YAML, self._data_dir),
                NamespaceEntry(file="internal_ns.yaml"),
            ),
        }
        config = WoodglueConfig(
            namespaces={
                "pub": NamespaceEntry(file="pub_ns.yaml"),
                "internal": NamespaceEntry(file="internal_ns.yaml"),
            }
        )
        return create_app(namespaces=namespaces, config=config)

    # ---- Positive: api-tagged methods appear in all 4 surfaces ----

    def _rpc_call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        body: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": 1}
        if params is not None:
            body["params"] = params
        resp = self.fetch("/rpc", method="POST", body=json.dumps(body))
        return json.loads(resp.body)

    def test_rpc_api_methods_callable(self):
        data = self._rpc_call("pub.hello", {"name": "test"})
        assert "result" in data
        assert data["result"] == 4

        data = self._rpc_call("pub.pydantic_hello", {"input": {"name": "Alice", "age": 30}})
        assert data["result"]["eman"] == "ecilA"
        assert data["result"]["ega"] == -30

        data = self._rpc_call("internal.greet", {"name": "hi"})
        assert "result" in data

    def test_llms_txt_lists_api_methods(self):
        resp = self.fetch("/docs/llms.txt")
        body = resp.body.decode()
        assert "pub.hello" in body
        assert "pub.pydantic_hello" in body
        assert "internal.greet" in body

    def test_markdown_api_methods_accessible(self):
        for name in ["pub.hello", "pub.pydantic_hello", "internal.greet"]:
            resp = self.fetch(f"/docs/methods/{name}.md")
            assert resp.code == 200, f"{name} should return 200"

    def test_openapi_has_api_methods(self):
        resp = self.fetch("/docs/openapi.json")
        spec = json.loads(resp.body)
        paths = spec["paths"]
        assert "/rpc/pub.hello" in paths
        assert "/rpc/pub.pydantic_hello" in paths
        assert "/rpc/internal.greet" in paths

    def test_openapi_has_x_global_ref(self):
        resp = self.fetch("/docs/openapi.json")
        spec = json.loads(resp.body)
        op = spec["paths"]["/rpc/pub.pydantic_hello"]["post"]
        resp_schema = op["responses"]["200"]["content"]["application/json"]["schema"]
        assert "x-global-ref" in resp_schema
        assert "HelloOut" in resp_schema["x-global-ref"]

    # ---- Negative: non-api methods excluded from all 4 surfaces ----

    def test_rpc_non_api_methods_not_found(self):
        data = self._rpc_call("pub.secret_hello", {"name": "test"})
        assert data["error"]["code"] == -32601

        data = self._rpc_call("internal.hidden_greet", {"name": "test"})
        assert data["error"]["code"] == -32601

    def test_llms_txt_excludes_non_api(self):
        resp = self.fetch("/docs/llms.txt")
        body = resp.body.decode()
        assert "secret_hello" not in body
        assert "hidden_greet" not in body

    def test_markdown_non_api_returns_404(self):
        resp = self.fetch("/docs/methods/pub.secret_hello.md")
        assert resp.code == 404
        resp = self.fetch("/docs/methods/internal.hidden_greet.md")
        assert resp.code == 404

    def test_openapi_excludes_non_api(self):
        resp = self.fetch("/docs/openapi.json")
        spec = json.loads(resp.body)
        paths_str = json.dumps(spec["paths"])
        assert "secret_hello" not in paths_str
        assert "hidden_greet" not in paths_str

    # ---- Client ----

    @tornado.testing.gen_test
    async def test_client_load_spec_and_call(self):
        client = WoodglueClient(self.get_url(""))
        await client.load_spec(strict=True)
        result = await client.call("pub.pydantic_hello", input={"name": "Alice", "age": 30})
        assert isinstance(result, HelloOut)
        assert result.eman == "ecilA"

    @tornado.testing.gen_test
    async def test_client_non_api_raises_error(self):
        client = WoodglueClient(self.get_url(""))
        try:
            await client.call("pub.secret_hello", name="test")
            raise AssertionError("Expected WoodglueRpcError")
        except WoodglueRpcError as e:
            assert e.code == -32601
