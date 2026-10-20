"""
JSON-RPC 2.0 handler backed by a lythonic Namespace.

Dispatches JSON-RPC method calls to NamespaceNode callables, validates
parameters against Method.args, and returns standard JSON-RPC 2.0 responses.

Every request runs as a `woodglue.access.Caller` (see `woodglue.apps.authn`):

- No valid credential: `-32000 "Unauthorized"`.
- A Session-cookie request that is not `application/json` or comes from
  another origin: `-32003 "Cross-site request rejected"`.
- A namespace the Caller cannot see is reported as `-32601 "Method not found"`,
  so its existence does not leak.
- A visible namespace without a `call` Grant: `-32003 "Forbidden: ..."`.
- DAG runs a call starts, and trigger changes it makes, record the Caller's
  User id as `initiated_by` (`anonymous` when auth is disabled).
- `system.*` methods check their own Grants (see `woodglue.apps.system_api`)
  and report a missing one as `-32003` too.

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
from lythonic.compose.dag_provenance import initiated_by
from pydantic import BaseModel, ValidationError
from typing_extensions import override

from woodglue.access import AccessDenied, Action, Caller, current_caller
from woodglue.apps.authn import AuthVia, authenticate, is_same_origin

logger = logging.getLogger(__name__)

SYSTEM_PREFIX = "system"

# JSON-RPC 2.0 standard error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Server-defined error codes
UNAUTHORIZED = -32000
FORBIDDEN = -32003


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

    caller: Caller | None = None

    @override
    def prepare(self) -> None:
        self.set_header("Content-Type", "application/json")
        auth = authenticate(self)
        self.caller = auth.caller
        if self.caller is None:
            self._write_unauthorized()
            return
        # A cross-site form or `fetch` cannot send JSON without a CORS preflight,
        # which this server never answers, so the content type blocks most CSRF;
        # the origin check covers the rest.
        if auth.via == AuthVia.cookie and not (
            self.request.headers.get("Content-Type", "").startswith("application/json")
            and is_same_origin(self.request)
        ):
            self.write(_error_response(FORBIDDEN, "Cross-site request rejected"))
            self.finish()

    def _write_unauthorized(self) -> None:
        self.write(
            {
                "jsonrpc": "2.0",
                "error": {"code": UNAUTHORIZED, "message": "Unauthorized"},
                "id": None,
            }
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
        if node is None or (
            prefix != SYSTEM_PREFIX and self.caller is not None and not self.caller.can_see(prefix)
        ):
            self.write(_error_response(METHOD_NOT_FOUND, f"Method not found: {method}", request_id))
            return
        # System methods check the action they need against the namespace they touch.
        if prefix != SYSTEM_PREFIX and self.caller is not None:
            if not self.caller.can(Action.call, prefix):
                denied = AccessDenied(Action.call, prefix)
                self.write(_error_response(FORBIDDEN, str(denied), request_id))
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
        caller_token = current_caller.set(self.caller)

        # Call the method, attributing any runs it starts to the Caller
        try:
            with initiated_by(self.caller.user_id or "anonymous" if self.caller else None):
                result = node(**kwargs)
                if inspect.isawaitable(result):
                    result = await result
        except AccessDenied as exc:
            logger.warning("Access denied calling %s: %s", method, exc)
            self.write(_error_response(FORBIDDEN, str(exc), request_id))
            return
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
            current_caller.reset(caller_token)
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
