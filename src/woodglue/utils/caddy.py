from pathlib import Path
from typing import Annotated, Any, Literal

import requests
from pydantic import BaseModel, ConfigDict, DirectoryPath, Field, field_validator


class CaddyMatcherConfig(BaseModel):
    host: None | list[str] = Field(default=None)
    path: None | list[str] = Field(default=None)
    method: None | list[str] = Field(default=None)
    protocol: None | str = Field(default=None)


class FileServerHandler(BaseModel):
    handler: Literal["file_server"] = "file_server"
    root: None | str | Path = Field(default=None)


class SubrouteHandler(BaseModel):
    handler: Literal["subroute"] = "subroute"
    routes: list["RouteConfig"] = Field(default_factory=list)


class EncodeHandler(BaseModel):
    handler: Literal["encode"] = "encode"
    encodings: dict[str, dict[str, Any]]
    prefer: list[str] = Field(default_factory=list)


class VarsHandler(BaseModel):
    model_config: ConfigDict = ConfigDict(extra="allow")  # pyright: ignore[reportIncompatibleVariableOverride]

    handler: Literal["vars"] = "vars"
    # All additional properties will be captured in model_extra (Pydantic v2+) or .__pydantic_extra__


class Upstream(BaseModel):
    dial: str
    max_requests: None | int = Field(default=None)


class ReverseProxyHandler(BaseModel):
    handler: Literal["reverse_proxy"] = "reverse_proxy"
    upstreams: list[Upstream] = Field(default_factory=list)


"""
{
	"handler": "static_response",
	"body": "I can do hard things."
}
"""


class StaticResponseHandler(BaseModel):
    handler: Literal["static_response"] = "static_response"
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


def _hh(*hh: Handler) -> list[Handler]:
    return list(hh)


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


class EnsureStaticSite(BaseModel):
    directory: DirectoryPath
    domains: list[str]
    routes: dict[str, str]

    @field_validator("domains")
    @classmethod
    def validate_domains(cls, domains: list[str]) -> list[str]:
        if not domains:
            raise ValueError("At least one domain must be specified")
        return domains

    @field_validator("routes")
    @classmethod
    def validate_routes(cls, routes: dict[str, str]) -> dict[str, str]:
        if not routes:
            raise ValueError("At least one route must be specified")
        return routes

    @property
    def server_id(self) -> str:
        return self.domains[0]

    def _route_config(self) -> RouteConfig:
        return RouteConfig(
            match=[CaddyMatcherConfig(host=self.domains)],
            handle=_hh(
                SubrouteHandler(
                    routes=[
                        RouteConfig(
                            handle=_hh(
                                FileServerHandler(handler="file_server", root=self.directory)
                            )
                        )
                    ]
                )
            ),
        )

    def server_config(self) -> ServerConfig:
        return ServerConfig(listen=[":433"], routes=[self._route_config()], id=self.server_id)  # pyright: ignore[reportCallIssue]


def ensure_static_site(req: EnsureStaticSite) -> None:
    config = get_caddy_config()
    new_config = req.server_config()
    if req.server_id in config.apps.http.servers:
        print(
            f"Replacing server {req.server_id}:\n{config.apps.http.servers[req.server_id]}\n   with new config:\n "
        )
    print(new_config)
    set_caddy_config(new_config)


def get_caddy_config() -> CaddyConfig:
    response = requests.get(f"{api_base_url}/config")
    # print(response.text)
    return response.json()


def set_caddy_config(config: CaddyConfig | ServerConfig) -> None:
    url = f"{api_base_url}/config"
    if isinstance(config, ServerConfig):
        assert config.id is not None, "ServerConfig must have an id"
        url = f"{api_base_url}/config/apps/http/servers/{config.id}"
    response = requests.post(url, json=config.model_dump_json(by_alias=True, exclude_none=True))
    return response.json()
