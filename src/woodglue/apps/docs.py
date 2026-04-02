"""
OpenAPI spec generation and inline HTML docs for a lythonic Namespace.
"""

from __future__ import annotations

import html
import inspect
from collections.abc import Iterator
from typing import Any

import tornado.web
from lythonic.compose import Method
from lythonic.compose.namespace import Namespace, NamespaceNode
from pydantic import BaseModel
from typing_extensions import override

# ---------------------------------------------------------------------------
# walk_namespace
# ---------------------------------------------------------------------------


def walk_namespace(ns: Namespace) -> Iterator[NamespaceNode]:
    """Recursively yield every NamespaceNode in *ns*."""
    # Namespace has no public iteration API; private attrs are the only option.
    for _name, node in ns._leaves.items():  # pyright: ignore[reportPrivateUsage]
        yield node
    for _branch_name, branch in ns._branches.items():  # pyright: ignore[reportPrivateUsage]
        yield from walk_namespace(branch)


# ---------------------------------------------------------------------------
# python_type_to_schema
# ---------------------------------------------------------------------------


def python_type_to_schema(annotation: Any) -> dict[str, Any]:
    """Map a Python type annotation to a JSON Schema fragment."""
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
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation.model_json_schema()
    return {"type": "string"}


def _type_display(annotation: Any) -> str:
    """Human-readable name for a type annotation."""
    if annotation is None or annotation is inspect.Parameter.empty:
        return "str"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)


# ---------------------------------------------------------------------------
# generate_openapi_spec
# ---------------------------------------------------------------------------


def generate_openapi_spec(ns: Namespace) -> dict[str, Any]:
    """Build an OpenAPI 3.0.3 spec dict from a Namespace."""
    paths: dict[str, Any] = {}

    for node in walk_namespace(ns):
        method: Method = node.method
        nsref: str = node.nsref
        path = f"/rpc/{nsref}"

        # --- request body schema from ArgInfo ---------------------------------
        properties: dict[str, Any] = {}
        required: list[str] = []
        for arg in method.args:
            prop = python_type_to_schema(arg.annotation)
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

        # --- response schema --------------------------------------------------
        ret = method.return_annotation
        if ret is None or ret is inspect.Parameter.empty:
            response_schema = {"type": "object"}
        else:
            response_schema = python_type_to_schema(ret)

        # --- operation --------------------------------------------------------
        summary = method.doc or ""
        operation: dict[str, Any] = {
            "summary": summary.split("\n")[0].strip() if summary else nsref,
            "operationId": nsref,
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
        if summary:
            operation["description"] = summary

        paths[path] = {"post": operation}

    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Woodglue JSON-RPC API",
            "version": "1.0.0",
        },
        "paths": paths,
    }


def _json_safe_default(value: Any) -> Any:
    """Return a JSON-serialisable representation of *value*."""
    if isinstance(value, str | int | float | bool | type(None)):
        return value
    return str(value)


# ---------------------------------------------------------------------------
# Tornado handlers
# ---------------------------------------------------------------------------


class DocsHandler(tornado.web.RequestHandler):
    """GET /docs -> OpenAPI JSON spec."""

    @override
    def get(self) -> None:
        ns = self.application.settings["namespace"]
        self.set_header("Content-Type", "application/json")
        self.write(generate_openapi_spec(ns))


class DocsUiHandler(tornado.web.RequestHandler):
    """GET /docs/ui -> self-contained inline HTML docs page."""

    @override
    def get(self) -> None:
        ns = self.application.settings["namespace"]
        methods_html = _build_methods_html(ns)
        page = _HTML_TEMPLATE.replace("{{methods}}", methods_html)
        self.set_header("Content-Type", "text/html; charset=utf-8")
        self.write(page)


# ---------------------------------------------------------------------------
# HTML generation helpers
# ---------------------------------------------------------------------------


def _build_methods_html(ns: Namespace) -> str:
    parts: list[str] = []
    for node in walk_namespace(ns):
        m = node.method
        doc = html.escape(m.doc or "", quote=True)
        ret = _type_display(m.return_annotation)

        rows = ""
        for arg in m.args:
            default_str = (
                ""
                if (arg.default is None or arg.default is inspect.Parameter.empty)
                else html.escape(repr(arg.default))
            )
            rows += (
                "<tr>"
                f"<td>{html.escape(arg.name)}</td>"
                f"<td><code>{html.escape(_type_display(arg.annotation))}</code></td>"
                f"<td>{default_str}</td>"
                f"<td>{html.escape(arg.description)}</td>"
                "</tr>"
            )

        parts.append(
            f'<div class="method">'
            f"<h2><code>POST /rpc/{html.escape(node.nsref)}</code></h2>"
            f'<p class="doc">{doc}</p>'
            f"<table>"
            f"<thead><tr><th>Parameter</th><th>Type</th><th>Default</th><th>Description</th></tr></thead>"
            f"<tbody>{rows}</tbody>"
            f"</table>"
            f'<p class="ret">Returns: <code>{html.escape(ret)}</code></p>'
            f"</div>"
        )
    return "\n".join(parts)


_HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Woodglue API Docs</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         color: #1a1a1a; background: #f5f5f5; padding: 2rem; line-height: 1.5; }
  h1 { margin-bottom: 1.5rem; font-size: 1.6rem; }
  .method { background: #fff; border: 1px solid #ddd; border-radius: 6px;
            padding: 1.2rem 1.4rem; margin-bottom: 1.2rem; }
  .method h2 { font-size: 1rem; margin-bottom: .5rem; }
  .method .doc { color: #555; margin-bottom: .8rem; white-space: pre-wrap; }
  .method .ret { margin-top: .6rem; font-size: .9rem; color: #333; }
  table { width: 100%; border-collapse: collapse; font-size: .9rem; }
  th, td { text-align: left; padding: .35rem .6rem; border-bottom: 1px solid #eee; }
  th { background: #fafafa; font-weight: 600; }
  code { background: #f0f0f0; padding: .1rem .3rem; border-radius: 3px; font-size: .85em; }
</style>
</head>
<body>
<h1>Woodglue API Documentation</h1>
{{methods}}
</body>
</html>
"""
