"""Tests for woodglue.apps.llm_docs generation."""

from lythonic.compose.namespace import Namespace
from pydantic import BaseModel

from woodglue.apps.llm_docs import (
    build_method_index,
    generate_llms_txt,
    generate_method_markdown,
    generate_openapi_spec,
)


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


def _make_namespaces() -> dict[str, Namespace]:
    ns1 = Namespace()
    ns1.register(create_item, nsref="create_item")

    ns2 = Namespace()
    ns2.register(simple_add, nsref="simple_add")

    return {"items": ns1, "math": ns2}


def test_generate_llms_txt():
    namespaces = _make_namespaces()
    txt = generate_llms_txt(namespaces)
    assert "# Woodglue API" in txt
    assert "- items.create_item: Create an item from input." in txt
    assert "- math.simple_add: Add two numbers." in txt


def _identity(x: str) -> str:  # pyright: ignore[reportUnusedParameter]
    return x


def test_generate_llms_txt_no_docstring():
    """Methods without docstrings use the qualified name as teaser."""
    ns = Namespace()
    ns.register(_identity, nsref="identity")
    txt = generate_llms_txt({"misc": ns})
    assert "- misc.identity:" in txt


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
    spec = generate_openapi_spec(namespaces)
    assert spec["openapi"] == "3.0.3"
    assert "/rpc/items.create_item" in spec["paths"] or "items.create_item" in str(spec)
    assert "/rpc/math.simple_add" in spec["paths"] or "math.simple_add" in str(spec)
