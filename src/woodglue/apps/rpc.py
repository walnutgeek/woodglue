"""
JSON-RPC 2.0 handler backed by a lythonic Namespace.

Dispatches JSON-RPC method calls to NamespaceNode callables, validates
parameters against Method.args, and returns standard JSON-RPC 2.0 responses.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any

import tornado.web
from pydantic import BaseModel
from typing_extensions import override

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 standard error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


def _error_response(code: int, message: str, request_id: Any = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
        "id": request_id,
    }


def _serialize_result(result: Any) -> Any:
    """Serialize a result for JSON-RPC response."""
    if result is None or isinstance(result, str | int | float | bool):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, list | dict):
        return result
    return str(result)


class JsonRpcHandler(tornado.web.RequestHandler):
    """Tornado handler that speaks JSON-RPC 2.0 over HTTP POST.

    Expects ``self.application.settings['namespace']`` to be a
    ``lythonic.compose.namespace.Namespace`` instance.
    """

    @override
    def prepare(self) -> None:
        self.set_header("Content-Type", "application/json")

    @override
    async def post(self) -> None:
        request_id: Any = None

        # 1. Parse JSON body
        try:
            body = json.loads(self.request.body)
        except (json.JSONDecodeError, TypeError):
            self.write(_error_response(PARSE_ERROR, "Parse error"))
            return

        # Batch requests are not supported
        if isinstance(body, list):
            self.write(_error_response(INVALID_REQUEST, "Batch requests are not supported"))
            return

        request_id = body.get("id")

        # 2. Validate required fields
        if not isinstance(body, dict) or body.get("jsonrpc") != "2.0" or "method" not in body:
            self.write(
                _error_response(
                    INVALID_REQUEST,
                    "Invalid Request: missing 'jsonrpc' or 'method'",
                    request_id,
                )
            )
            return

        method: str = body["method"]
        params: Any = body.get("params")

        # 3. Look up method in namespace
        ns = self.application.settings["namespace"]  # type: ignore[union-attr]
        try:
            node = ns.get(method)
        except KeyError:
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return

        # 4. Build kwargs from params
        kwargs: dict[str, Any] = {}
        method_args = node.method.args

        if params is not None:
            if isinstance(params, list):
                # Positional params: zip with declared arg names
                for arg_info, value in zip(method_args, params, strict=False):
                    kwargs[arg_info.name] = value
            elif isinstance(params, dict):
                kwargs = dict(params)
            else:
                self.write(
                    _error_response(
                        INVALID_PARAMS,
                        "params must be an array or object",
                        request_id,
                    )
                )
                return

        # 5. Validate required params
        for arg_info in method_args:
            if not arg_info.is_optional and arg_info.name not in kwargs:
                self.write(
                    _error_response(
                        INVALID_PARAMS,
                        f"Missing required parameter: {arg_info.name}",
                        request_id,
                    )
                )
                return

        # 6. Call the method
        try:
            result = node(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except Exception:
            logger.exception("Internal error calling %s", method)
            self.write(_error_response(INTERNAL_ERROR, "Internal error", request_id))
            return

        # 7. Return result
        self.write(
            {
                "jsonrpc": "2.0",
                "result": _serialize_result(result),
                "id": request_id,
            }
        )
