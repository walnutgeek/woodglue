"""Tests for woodglue.client.WoodglueClient."""

import tornado.testing
from lythonic.compose.namespace import Namespace
from typing_extensions import override

from woodglue.apps.server import create_app
from woodglue.client import WoodglueClient, WoodglueRpcError
from woodglue.config import WoodglueConfig
from woodglue.hello import HelloIn, HelloOut, hello, pydantic_hello


def _make_namespaces() -> dict[str, Namespace]:
    ns = Namespace()
    ns.register(hello, nsref="hello", tags=["api"])
    ns.register(pydantic_hello, nsref="pydantic_hello", tags=["api"])
    return {"test": ns}


class TestWoodglueClient(tornado.testing.AsyncHTTPTestCase):
    @override
    def get_app(self):
        config = WoodglueConfig(namespaces={"test": "unused"})
        return create_app(namespaces=_make_namespaces(), config=config)

    @tornado.testing.gen_test
    async def test_call_simple_method(self):
        client = WoodglueClient(self.get_url(""))
        result = await client.call("test.hello", name="World")
        assert result == 5

    @tornado.testing.gen_test
    async def test_call_basemodel_method_raw(self):
        """Without spec loading, returns raw dict."""
        client = WoodglueClient(self.get_url(""))
        result = await client.call("test.pydantic_hello", input={"name": "Alice", "age": 30})
        assert result == {"eman": "ecilA", "ega": -30}

    @tornado.testing.gen_test
    async def test_call_with_return_type(self):
        """Explicit return_type deserializes the result."""
        client = WoodglueClient(self.get_url(""))
        result = await client.call(
            "test.pydantic_hello", input={"name": "Alice", "age": 30}, return_type=HelloOut
        )
        assert isinstance(result, HelloOut)
        assert result.eman == "ecilA"
        assert result.ega == -30

    @tornado.testing.gen_test
    async def test_call_with_spec_loading(self):
        """After load_spec, return types are auto-resolved."""
        client = WoodglueClient(self.get_url(""))
        await client.load_spec(strict=True)
        result = await client.call("test.pydantic_hello", input={"name": "Bob", "age": 25})
        assert isinstance(result, HelloOut)
        assert result.eman == "boB"

    @tornado.testing.gen_test
    async def test_call_method_not_found(self):
        """Calling a non-existent method raises WoodglueRpcError."""
        client = WoodglueClient(self.get_url(""))
        try:
            await client.call("test.nonexistent")
            raise AssertionError("Expected WoodglueRpcError")
        except WoodglueRpcError as e:
            assert e.code == -32601

    @tornado.testing.gen_test
    async def test_call_with_resolver(self):
        """Custom resolver overrides spec resolution."""
        client = WoodglueClient(self.get_url(""))
        await client.load_spec(strict=True)

        def resolver(gref: str) -> type | None:
            if "HelloOut" in gref:
                return HelloOut
            return None

        client._return_types.clear()  # pyright: ignore[reportPrivateUsage]
        result = await client.call(
            "test.pydantic_hello", input={"name": "Eve", "age": 20}, resolver=resolver
        )
        assert isinstance(result, HelloOut)
        assert result.eman == "evE"

    @tornado.testing.gen_test
    async def test_load_spec_strict_succeeds(self):
        """strict=True succeeds when all grefs are resolvable."""
        client = WoodglueClient(self.get_url(""))
        await client.load_spec(strict=True)
        assert "test.pydantic_hello" in client._return_types  # pyright: ignore[reportPrivateUsage]

    @tornado.testing.gen_test
    async def test_basemodel_input_serialized(self):
        """BaseModel kwargs are serialized before sending."""
        client = WoodglueClient(self.get_url(""))
        result = await client.call(
            "test.pydantic_hello", input=HelloIn(name="Zoe", age=10), return_type=HelloOut
        )
        assert isinstance(result, HelloOut)
        assert result.eman == "eoZ"
