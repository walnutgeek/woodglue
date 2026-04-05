"""
Async JSON-RPC 2.0 client for woodglue servers.

`WoodglueClient` makes typed RPC calls, optionally resolving return types
from `x-global-ref` in the OpenAPI spec. Uses Tornado's `AsyncHTTPClient`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel
from tornado.httpclient import AsyncHTTPClient, HTTPRequest


class WoodglueRpcError(Exception):
    """Raised when the server returns a JSON-RPC error response."""

    def __init__(self, code: int, message: str):
        self.code: int = code
        self.message: str = message
        super().__init__(f"JSON-RPC error {code}: {message}")


class WoodglueClient:
    """
    Async client for woodglue JSON-RPC servers.

    Optionally loads the OpenAPI spec to auto-resolve return types from
    `x-global-ref` vendor extensions.

    Resolution priority on `call()`:
    1. Explicit `return_type` parameter
    2. `resolver` callable (receives the `x-global-ref` string)
    3. Type from `load_spec()` if loaded
    4. Return raw dict/primitive
    """

    def __init__(self, base_url: str):
        self._base_url: str = base_url.rstrip("/")
        self._http: AsyncHTTPClient = AsyncHTTPClient()
        self._request_id: int = 0
        self._return_types: dict[str, type[BaseModel]] = {}
        self._return_grefs: dict[str, str] = {}

    async def load_spec(self, strict: bool = False) -> None:
        """
        Fetch `/docs/openapi.json` and resolve `x-global-ref` types.

        With `strict=True`, raises `ImportError` if any gref cannot be
        resolved. With `strict=False`, skips unresolvable grefs.
        """
        from lythonic import GlobalRef

        resp = await self._http.fetch(f"{self._base_url}/docs/openapi.json")
        spec = json.loads(resp.body)

        for _path, path_item in spec.get("paths", {}).items():
            for _http_method, operation in path_item.items():
                op_id = operation.get("operationId")
                if not op_id:
                    continue

                resp_content = (
                    operation.get("responses", {})
                    .get("200", {})
                    .get("content", {})
                    .get("application/json", {})
                )
                schema = resp_content.get("schema", {})
                gref_str = schema.get("x-global-ref")
                if not gref_str:
                    continue

                self._return_grefs[op_id] = gref_str

                try:
                    gref = GlobalRef(gref_str)
                    cls = gref.get_instance()
                    if isinstance(cls, type) and issubclass(cls, BaseModel):
                        self._return_types[op_id] = cls
                except Exception as exc:
                    if strict:
                        raise ImportError(
                            f"Cannot resolve x-global-ref '{gref_str}' for method '{op_id}'"
                        ) from exc

    async def call(
        self,
        method: str,
        *,
        return_type: type[BaseModel] | None = None,
        resolver: Callable[[str], type[BaseModel] | None] | None = None,
        **kwargs: Any,
    ) -> Any:
        """
        Call a JSON-RPC method and return the deserialized result.

        `kwargs` are sent as the JSON-RPC `params` object. BaseModel
        values in kwargs are serialized via `model_dump(mode="json")`.
        """
        self._request_id += 1

        params: dict[str, Any] = {}
        for key, value in kwargs.items():
            if isinstance(value, BaseModel):
                params[key] = value.model_dump(mode="json")
            else:
                params[key] = value

        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": self._request_id,
            }
        )

        req = HTTPRequest(
            f"{self._base_url}/rpc",
            method="POST",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        resp = await self._http.fetch(req)
        data = json.loads(resp.body)

        if "error" in data:
            err = data["error"]
            raise WoodglueRpcError(err["code"], err["message"])

        result = data.get("result")

        resolved_type = return_type
        if resolved_type is None and resolver is not None:
            gref_str = self._return_grefs.get(method)
            if gref_str:
                resolved_type = resolver(gref_str)
        if resolved_type is None:
            resolved_type = self._return_types.get(method)

        if resolved_type is not None and isinstance(result, dict):
            return resolved_type.model_validate(result)
        return result
