"""
LLM-friendly documentation generation for woodglue namespaces.

Generates three artifact types:
- `llms.txt`: index of all methods with one-line teasers
- Per-method markdown: full docs with parameters, return types, referenced models
- OpenAPI 3.0.3 spec: standard API spec
"""

from __future__ import annotations

import inspect
from typing import Any

import tornado.web
from lythonic.compose import Method
from lythonic.compose.namespace import Namespace, NamespaceNode
from pydantic import BaseModel
from typing_extensions import override


def walk_namespace(ns: Namespace) -> list[tuple[str, NamespaceNode]]:
    """
    Return `(leaf_name, node)` pairs for every NamespaceNode in `ns`,
    recursively. `leaf_name` is the short key in the namespace, not the
    full module-qualified nsref.
    """
    result: list[tuple[str, NamespaceNode]] = []
    for name, node in ns._leaves.items():  # pyright: ignore[reportPrivateUsage]
        result.append((name, node))
    for _branch_name, branch in ns._branches.items():  # pyright: ignore[reportPrivateUsage]
        result.extend(walk_namespace(branch))
    return result


API_TAG = "api"


def build_method_index(
    namespaces: dict[str, Namespace],
) -> dict[str, dict[str, NamespaceNode]]:
    """
    Build a flat lookup: `{prefix: {leaf_name: node}}`.

    Only includes methods tagged with `"api"`. This resolves the nested
    namespace structure into a simple two-level dict suitable for both
    RPC dispatch and documentation generation.
    """
    index: dict[str, dict[str, NamespaceNode]] = {}
    for prefix, ns in namespaces.items():
        methods: dict[str, NamespaceNode] = {}
        for leaf_name, node in walk_namespace(ns):
            if API_TAG in node.tags:
                methods[leaf_name] = node
        if methods:
            index[prefix] = methods
    return index


def _docstring_teaser(doc: str | None) -> str:
    """Extract the first line of a docstring as a teaser."""
    if not doc:
        return ""
    return doc.strip().split("\n")[0].strip()


def _type_display(annotation: Any) -> str:
    """Human-readable name for a type annotation."""
    if annotation is None or annotation is inspect.Parameter.empty:
        return "str"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


def _is_basemodel(annotation: Any) -> bool:
    """Check if annotation is a BaseModel subclass."""
    return isinstance(annotation, type) and issubclass(annotation, BaseModel)


def _collect_referenced_models(method: Method) -> list[type[BaseModel]]:
    """
    Collect all BaseModel types referenced by a method's args and return type,
    recursively expanding nested models.
    """
    seen: set[type[BaseModel]] = set()
    queue: list[type[BaseModel]] = []

    for arg in method.args:
        ann = arg.annotation
        if _is_basemodel(ann):
            assert isinstance(ann, type) and issubclass(ann, BaseModel)
            if ann not in seen:
                seen.add(ann)
                queue.append(ann)

    ret = method.return_annotation
    if _is_basemodel(ret):
        assert isinstance(ret, type) and issubclass(ret, BaseModel)
        if ret not in seen:
            seen.add(ret)
            queue.append(ret)

    # Expand nested models
    i = 0
    while i < len(queue):
        model = queue[i]
        for field_info in model.model_fields.values():
            field_ann = field_info.annotation
            if _is_basemodel(field_ann):
                assert isinstance(field_ann, type) and issubclass(field_ann, BaseModel)
                if field_ann not in seen:
                    seen.add(field_ann)
                    queue.append(field_ann)
        i += 1

    return queue


def _render_model_table(model: type[BaseModel]) -> str:
    """Render a BaseModel's fields as a markdown table."""
    lines = [
        f"### {model.__name__}",
        "",
        "| Field | Type | Required | Default | Description |",
        "|-------|------|----------|---------|-------------|",
    ]
    for name, field in model.model_fields.items():
        ftype = _type_display(field.annotation)
        required = "yes" if field.is_required() else "no"
        default = "-" if field.is_required() else repr(field.default)
        desc = field.description or "-"
        lines.append(f"| {name} | {ftype} | {required} | {default} | {desc} |")
    return "\n".join(lines)


# ---- llms.txt generation ----


def generate_llms_txt(method_index: dict[str, dict[str, NamespaceNode]]) -> str:
    """
    Generate an `llms.txt` index listing all methods in the method index.
    """
    lines = [
        "# Woodglue API",
        "",
        "> JSON-RPC 2.0 server",
        "",
        "## Methods",
        "",
    ]

    for prefix in sorted(method_index):
        for leaf_name, node in sorted(method_index[prefix].items()):
            teaser = _docstring_teaser(node.method.doc)
            qualified = f"{prefix}.{leaf_name}"
            doc_link = f"/docs/methods/{qualified}.md"
            if teaser:
                lines.append(f"- [{qualified}]({doc_link}): {teaser}")
            else:
                lines.append(f"- [{qualified}]({doc_link})")
    lines.append("")
    return "\n".join(lines)


# ---- Per-method markdown generation ----


def generate_method_markdown(prefix: str, method_name: str, node: NamespaceNode) -> str:
    """
    Generate full markdown documentation for a single method.
    """
    method = node.method
    qualified = f"{prefix}.{method_name}"

    doc = method.doc or ""
    full_doc = doc.strip() if doc else ""

    lines = [
        f"# {qualified}",
        "",
        full_doc,
        "",
        "## Parameters",
        "",
        "| Name | Type | Required | Description |",
        "|------|------|----------|-------------|",
    ]

    for arg in method.args:
        atype = _type_display(arg.annotation)
        required = "no" if arg.is_optional else "yes"
        desc = arg.description or "-"
        lines.append(f"| {arg.name} | {atype} | {required} | {desc} |")

    # Return type
    ret = method.return_annotation
    if ret is not None and ret is not inspect.Parameter.empty:
        lines.extend(["", "## Returns", "", f"`{_type_display(ret)}`"])

    # Referenced models
    models = _collect_referenced_models(method)
    if models:
        lines.extend(["", "## Referenced Models", ""])
        for model in models:
            lines.append(_render_model_table(model))
            lines.append("")

    lines.append("")
    return "\n".join(lines)


# ---- OpenAPI generation ----


def _python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema fragment.

    For BaseModel types, includes an `x-global-ref` vendor extension
    with the fully qualified module path for smart client deserialization.
    """
    if annotation is None or annotation is inspect.Parameter.empty:
        return {"type": "string"}
    if annotation is int:
        return {"type": "integer"}
    if annotation is float:
        return {"type": "number"}
    if annotation is str:
        return {"type": "string"}
    if annotation is bool:
        return {"type": "boolean"}
    if _is_basemodel(annotation):
        schema = annotation.model_json_schema()
        schema["x-global-ref"] = f"{annotation.__module__}:{annotation.__qualname__}"
        return schema
    return {"type": "string"}


def _json_safe_default(value: Any) -> Any:
    """Return a JSON-serializable representation of a default value."""
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    return str(value)


def generate_openapi_spec(
    method_index: dict[str, dict[str, NamespaceNode]],
) -> dict[str, Any]:
    """Build an OpenAPI 3.0.3 spec dict from the method index."""
    paths: dict[str, Any] = {}

    for prefix in sorted(method_index):
        for leaf_name, node in sorted(method_index[prefix].items()):
            method = node.method
            qualified = f"{prefix}.{leaf_name}"
            path = f"/rpc/{qualified}"

            properties: dict[str, Any] = {}
            required: list[str] = []
            for arg in method.args:
                prop = _python_type_to_schema(arg.annotation)
                if arg.description:
                    prop["description"] = arg.description
                if arg.default is not None and arg.default is not inspect.Parameter.empty:
                    prop["default"] = _json_safe_default(arg.default)
                properties[arg.name] = prop
                if not arg.is_optional:
                    required.append(arg.name)

            request_body_schema: dict[str, Any] = {
                "type": "object",
                "properties": properties,
            }
            if required:
                request_body_schema["required"] = required

            ret = method.return_annotation
            if ret is None or ret is inspect.Parameter.empty:
                response_schema: dict[str, Any] = {"type": "object"}
            else:
                response_schema = _python_type_to_schema(ret)

            summary = _docstring_teaser(method.doc)
            operation: dict[str, Any] = {
                "summary": summary or qualified,
                "operationId": qualified,
                "requestBody": {
                    "required": bool(required),
                    "content": {
                        "application/json": {
                            "schema": request_body_schema,
                        }
                    },
                },
                "responses": {
                    "200": {
                        "description": "Successful response",
                        "content": {
                            "application/json": {
                                "schema": response_schema,
                            }
                        },
                    }
                },
            }
            if method.doc:
                operation["description"] = method.doc.strip()

            paths[path] = {"post": operation}

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Woodglue JSON-RPC API",
            "version": "1.0.0",
        },
        "paths": paths,
    }


# ---- Tornado handlers ----


class LlmsTxtHandler(tornado.web.RequestHandler):
    """GET /docs/llms.txt"""

    @override
    def get(self) -> None:
        method_index: dict[str, dict[str, NamespaceNode]] = self.application.settings[
            "method_index"
        ]
        self.set_header("Content-Type", "text/plain; charset=utf-8")
        self.write(generate_llms_txt(method_index))


class MethodDocHandler(tornado.web.RequestHandler):
    """GET /docs/methods/{prefix}.{method}.md"""

    @override
    def get(self, filename: str) -> None:
        method_index: dict[str, dict[str, NamespaceNode]] = self.application.settings[
            "method_index"
        ]

        if not filename.endswith(".md"):
            raise tornado.web.HTTPError(404)
        name = filename[:-3]

        dot_pos = name.find(".")
        if dot_pos < 0:
            raise tornado.web.HTTPError(404)

        prefix = name[:dot_pos]
        method_name = name[dot_pos + 1 :]

        methods = method_index.get(prefix)
        if methods is None:
            raise tornado.web.HTTPError(404)

        node = methods.get(method_name)
        if node is None:
            raise tornado.web.HTTPError(404)

        md = generate_method_markdown(prefix, method_name, node)
        self.set_header("Content-Type", "text/markdown; charset=utf-8")
        self.write(md)


class OpenApiHandler(tornado.web.RequestHandler):
    """GET /docs/openapi.json"""

    @override
    def get(self) -> None:
        method_index: dict[str, dict[str, NamespaceNode]] = self.application.settings[
            "method_index"
        ]
        self.set_header("Content-Type", "application/json")
        self.write(generate_openapi_spec(method_index))
