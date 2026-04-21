"""Tests for woodglue.apps.llm_docs generation."""

from lythonic.compose.namespace import Namespace
from pydantic import BaseModel

from woodglue.apps.llm_docs import (
    build_method_index,
    generate_llms_txt,
    generate_method_markdown,
    generate_openapi_spec,
)
from woodglue.config import NamespaceEntry


class ItemIn(BaseModel):
    name: str
    count: int


class ItemOut(BaseModel):
    label: str
    total: int


def create_item(input: ItemIn) -> ItemOut:
    """Create an item from input.

    This is a longer description that explains
    the full behavior of the method.
    """
    return ItemOut(label=input.name, total=input.count)


def simple_add(a: int, b: int) -> int:
    """Add two numbers."""
    return a + b


def _make_namespaces() -> dict[str, tuple[Namespace, NamespaceEntry]]:
    ns1 = Namespace()
    ns1.register(create_item, nsref="create_item", tags=["api"])

    ns2 = Namespace()
    ns2.register(simple_add, nsref="simple_add", tags=["api"])

    return {
        "items": (ns1, NamespaceEntry(gref="dummy")),
        "math": (ns2, NamespaceEntry(gref="dummy")),
    }


def test_generate_llms_txt():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    txt = generate_llms_txt(index)
    assert "# Woodglue API" in txt
    assert (
        "- [items.create_item](/docs/methods/items.create_item.md): Create an item from input."
        in txt
    )
    assert "- [math.simple_add](/docs/methods/math.simple_add.md): Add two numbers." in txt


def _identity(x: str) -> str:  # pyright: ignore[reportUnusedParameter]
    return x


def test_generate_llms_txt_no_docstring():
    """Methods without docstrings use the qualified name as teaser."""
    ns = Namespace()
    ns.register(_identity, nsref="identity", tags=["api"])
    index = build_method_index({"misc": (ns, NamespaceEntry(gref="dummy"))})
    txt = generate_llms_txt(index)
    assert "- [misc.identity](/docs/methods/misc.identity.md)" in txt


def test_expose_api_false_excluded_from_method_index():
    """Namespaces with expose_api=False are excluded from the method index."""
    ns_exposed = Namespace()
    ns_exposed.register(simple_add, nsref="simple_add", tags=["api"])
    ns_hidden = Namespace()
    ns_hidden.register(create_item, nsref="create_item", tags=["api"])

    namespaces = {
        "exposed": (ns_exposed, NamespaceEntry(gref="dummy")),
        "hidden": (ns_hidden, NamespaceEntry(gref="dummy", expose_api=False)),
    }
    index = build_method_index(namespaces)
    assert "exposed" in index
    assert "hidden" not in index


def test_generate_method_markdown_with_basemodel():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    md = generate_method_markdown("items", "create_item", index["items"]["create_item"])
    assert "# items.create_item" in md
    assert "Create an item from input." in md
    assert "## Parameters" in md
    assert "| input | ItemIn |" in md
    assert "## Returns" in md
    assert "`ItemOut`" in md
    assert "## Referenced Models" in md
    assert "### ItemIn" in md
    assert "### ItemOut" in md
    assert "| name | str |" in md
    assert "| count | int |" in md
    assert "| label | str |" in md
    assert "| total | int |" in md


def test_generate_method_markdown_simple_types():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    md = generate_method_markdown("math", "simple_add", index["math"]["simple_add"])
    assert "# math.simple_add" in md
    assert "## Parameters" in md
    assert "| a | int |" in md
    assert "| b | int |" in md
    assert "## Referenced Models" not in md


def test_generate_openapi_spec():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    spec = generate_openapi_spec(index)
    assert spec["openapi"] == "3.0.3"
    assert "/rpc/items.create_item" in spec["paths"]
    assert "/rpc/math.simple_add" in spec["paths"]


def test_openapi_x_global_ref():
    namespaces = _make_namespaces()
    index = build_method_index(namespaces)
    spec = generate_openapi_spec(index)
    # Check x-global-ref on request body schema (BaseModel param)
    create_op = spec["paths"]["/rpc/items.create_item"]["post"]
    req_schema = create_op["requestBody"]["content"]["application/json"]["schema"]
    input_prop = req_schema["properties"]["input"]
    assert "x-global-ref" in input_prop
    assert input_prop["x-global-ref"].endswith(":ItemIn")
    # Check x-global-ref on response schema (BaseModel return)
    resp_schema = create_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert "x-global-ref" in resp_schema
    assert resp_schema["x-global-ref"].endswith(":ItemOut")
    # Simple types should NOT have x-global-ref
    add_op = spec["paths"]["/rpc/math.simple_add"]["post"]
    add_resp = add_op["responses"]["200"]["content"]["application/json"]["schema"]
    assert "x-global-ref" not in add_resp
