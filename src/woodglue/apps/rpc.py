"""
JSON-RPC 2.0 handler backed by a lythonic Namespace.

Dispatches JSON-RPC method calls to NamespaceNode callables, validates
parameters against Method.args, and returns standard JSON-RPC 2.0 responses.

Errors raised by a node:

- `RpcError` (or a subclass) reports an application error to the client:
  the response error object is `{"code": exc.code, "message": exc.message}`,
  plus `"data"` when `exc.data` is not `None` (serialized like results).
  It is logged at WARNING with the method name, code and message, without a
  traceback, since it is an expected, client-caused failure.
- Opt-in is by subclassing only. Any other exception, even one with an
  integer `code` attribute (e.g. `urllib.error.HTTPError`), becomes
  `-32603 "Internal error"` with no message leaked to the client, and is
  logged at ERROR with its traceback.
"""

from __future__ import annotations

import inspect
import json
import logging
from typing import Any, overload

import tornado.web
from pydantic import BaseModel, ValidationError
from typing_extensions import override

logger = logging.getLogger(__name__)

# JSON-RPC 2.0 standard error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


class RpcError(Exception):
    """
    Application-level JSON-RPC error that a node may raise to report a
    client-visible failure. `str(exc)` is the message.

    Construct as `RpcError(code, message, data=None)`, or set `code` as a
    class attribute on a subclass and construct it from just a message
    (`data` is then keyword-only):

    >>> class NotFound(RpcError):
    ...     code = -32001
    >>> e = NotFound("missing", data={"k": 1})
    >>> (e.code, e.message, e.data, str(e))
    (-32001, 'missing', {'k': 1}, 'missing')
    >>> RpcError(-32010, "x").code
    -32010
    """

    code: int
    message: str
    data: Any

    @overload
    def __init__(self, code: int, message: str, /, data: Any = None) -> None: ...
    @overload
    def __init__(self, message: str, /, *, data: Any = None) -> None: ...
    def __init__(
        self, code_or_message: int | str, message: str | None = None, /, data: Any = None
    ) -> None:
        if isinstance(code_or_message, str):
            if message is not None:
                raise TypeError("RpcError(message, ...) takes `data` only as a keyword")
            class_code = getattr(type(self), "code", None)
            if not isinstance(class_code, int):
                raise TypeError(f"{type(self).__name__} has no class-level `code`; pass one")
            message = code_or_message
        else:
            if message is None:
                raise TypeError("RpcError(code, message) requires a message")
            self.code = code_or_message
        self.message = message
        self.data = data
        super().__init__(message)


def _error_response(
    code: int, message: str, request_id: Any = None, data: Any = None
) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = _serialize_result(data)
    return {
        "jsonrpc": "2.0",
        "error": error,
        "id": request_id,
    }


def _serialize_result(result: Any) -> Any:
    """Serialize a result for JSON-RPC response."""
    if result is None or isinstance(result, str | int | float | bool):
        return result
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    if isinstance(result, list):
        return list(map(_serialize_result, result))
    if isinstance(result, dict):
        return {_serialize_result(k): _serialize_result(v) for k, v in result.items()}
    return str(result)


class JsonRpcHandler(tornado.web.RequestHandler):
    """Tornado handler that speaks JSON-RPC 2.0 over HTTP POST.

    Expects ``self.application.settings['namespaces']`` to be a dict mapping
    prefix strings to ``lythonic.compose.namespace.Namespace`` instances.
    """

    @override
    def prepare(self) -> None:
        self.set_header("Content-Type", "application/json")
        if not self.application.settings.get("auth_enabled", False):
            return
        auth_db = self.application.settings.get("auth_db")
        if auth_db is None:
            return
        token = self._extract_bearer_token()
        if not token:
            self._write_unauthorized()
            return
        from woodglue.token_store import validate_token

        if not validate_token(auth_db, token):
            self._write_unauthorized()
            return

    def _extract_bearer_token(self) -> str:
        auth_header = self.request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            return auth_header[7:].strip()
        return ""

    def _write_unauthorized(self) -> None:
        self.write(
            {"jsonrpc": "2.0", "error": {"code": -32000, "message": "Unauthorized"}, "id": None}
        )
        self.finish()

    @override
    async def post(self) -> None:
        request_id: Any = None

        # Parse JSON body
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

        # Validate required fields
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

        # Resolve namespace and method via dot prefix
        method_index: dict[str, dict[str, Any]] = self.application.settings["method_index"]

        dot_pos = method.find(".")
        if dot_pos < 0:
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return

        prefix = method[:dot_pos]
        method_name = method[dot_pos + 1 :]

        methods = method_index.get(prefix)
        if methods is None:
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return

        node = methods.get(method_name)
        if node is None:
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return

        # Build kwargs from params
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

        # Validate required params
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

        # Deserialize BaseModel params
        try:
            for arg_info in method_args:
                if (
                    arg_info.name in kwargs
                    and isinstance(arg_info.annotation, type)
                    and issubclass(arg_info.annotation, BaseModel)
                ):
                    kwargs[arg_info.name] = arg_info.annotation.model_validate(
                        kwargs[arg_info.name]
                    )
        except ValidationError as exc:
            self.write(
                _error_response(
                    INVALID_PARAMS,
                    f"Invalid parameters: {exc}",
                    request_id,
                )
            )
            return

        # Set current_mount context var for this namespace
        from woodglue.mount import MountContext, current_mount

        mounts: dict[str, MountContext] = self.application.settings.get("mounts", {})
        mount = mounts.get(prefix)
        token = current_mount.set(mount) if mount else None

        # Call the method
        try:
            result = node(**kwargs)
            if inspect.isawaitable(result):
                result = await result
        except RpcError as exc:
            # Expected, client-caused failure: no traceback.
            logger.warning("RPC error calling %s: %d %s", method, exc.code, exc.message)
            self.write(_error_response(exc.code, exc.message, request_id, exc.data))
            return
        except Exception:
            logger.exception("Internal error calling %s", method)
            self.write(_error_response(INTERNAL_ERROR, "Internal error", request_id))
            return
        finally:
            if token is not None:
                current_mount.reset(token)

        # Return result
        self.write(
            {
                "jsonrpc": "2.0",
                "result": _serialize_result(result),
                "id": request_id,
            }
        )
