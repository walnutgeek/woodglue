from typing import Annotated, Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, Field


class CaddyMatcherConfig(BaseModel):
    host: None | list[str] = Field(default=None)
    path: None | list[str] = Field(default=None)
    method: None | list[str] = Field(default=None)
    protocol: None | str = Field(default=None)


class FileServerHandler(BaseModel):
    handler: Literal["file_server"]
    root: None | str = Field(default=None)


class SubrouteHandler(BaseModel):
    handler: Literal["subroute"]
    routes: list["RouteConfig"] = Field(default_factory=list)


class EncodeHandler(BaseModel):
    handler: Literal["encode"]
    encodings: dict[str, dict[str, Any]]
    prefer: list[str] = Field(default_factory=list)


class VarsHandler(BaseModel):
    model_config: ConfigDict = ConfigDict(extra="allow")  # pyright: ignore[reportIncompatibleVariableOverride]

    handler: Literal["vars"]
    # All additional properties will be captured in model_extra (Pydantic v2+) or .__pydantic_extra__


class Upstream(BaseModel):
    dial: str
    max_requests: None | int = Field(default=None)


class ReverseProxyHandler(BaseModel):
    handler: Literal["reverse_proxy"]
    upstreams: list[Upstream] = Field(default_factory=list)


"""
{
	"handler": "static_response",
	"body": "I can do hard things."
}
"""


class StaticResponseHandler(BaseModel):
    handler: Literal["static_response"]
    body: str


Handler = Annotated[
    FileServerHandler
    | ReverseProxyHandler
    | SubrouteHandler
    | VarsHandler
    | EncodeHandler
    | StaticResponseHandler,
    Field(discriminator="handler"),
]


class RouteConfig(BaseModel):
    group: None | str = Field(default=None)
    match: None | list[CaddyMatcherConfig] = Field(default=None)
    handle: None | list[Handler] = Field(...)
    terminal: None | bool = Field(default=None)


class ServerConfig(BaseModel):
    model_config: ConfigDict = ConfigDict(populate_by_name=True)  # pyright: ignore[reportIncompatibleVariableOverride]

    id: None | str = Field(default=None, alias="@id")
    listen: list[str] = Field(default_factory=list)
    routes: None | list[RouteConfig] = Field(default=None)


class HTTPConfig(BaseModel):
    servers: dict[str, ServerConfig] = Field(default_factory=dict)


class AppsConfig(BaseModel):
    http: HTTPConfig = Field(default_factory=HTTPConfig)


class CaddyConfig(BaseModel):
    """
    subset of caddy config json API mainly focused on publishing static sites and reverse proxies
    """

    apps: AppsConfig = Field(default_factory=AppsConfig)


api_base_url = "http://localhost:2019"


def get_caddy_config() -> CaddyConfig:
    response = requests.get(f"{api_base_url}/config")
    return response.json()


def set_caddy_config(config: CaddyConfig | ServerConfig) -> None:
    url = f"{api_base_url}/config"
    if isinstance(config, ServerConfig):
        assert config.id is not None, "ServerConfig must have an id"
        url = f"{api_base_url}/config/apps/http/servers/{config.id}"
    response = requests.post(url, json=config.model_dump_json(by_alias=True, exclude_none=True))
    return response.json()
