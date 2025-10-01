from pathlib import Path
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field


class GitLocation(BaseModel):
    type: Literal["git"]
    repo: str
    branch: None | str = Field(default=None)


class PipLocation(BaseModel):
    type: Literal["pip"]
    package: str
    index: None | str = Field(default=None)
    extra_index: None | str = Field(default=None)


Location: TypeAlias = Annotated[GitLocation | PipLocation, Field(discriminator="type")]


class DeploymentConfig(BaseModel):
    domains: None | list[str] = Field(default=None)
    config_location: Location


class StaticSiteConfig(BaseModel):
    type: Literal["static-site"]
    domains: None | list[str] = Field(default=None)
    routes: dict[str, Path] = Field(default_factory=dict)


# class ReverseProxy(BaseModel):

# class Service(BaseModel):
#     methods: dict[str, GlobalRef]
#     public_api: bool

#     """Whether the service APIis published in caddy"""
#     static_sites: dict[str, Site]


# class ServiceCatalog:
#     services: dict[str, Service]

#     def __init__(self):
#         self.services = {}
