import sys
from pathlib import Path

from pydantic import BaseModel, Field

from woodglue.annotated import ActionTree, Main, RunContext

main_at = ActionTree(Main)


class Server(BaseModel):
    """Managing the server"""

    data: Path = Field(default=Path("./data"), description="directory to store all server data")


server_at = main_at.actions.add(Server)


@server_at.actions.wrap
def start(ctx: RunContext):
    """Starts the server in the foreground"""
    print(f"Starting server with data: {ctx.get('/server')}")


@server_at.actions.wrap
def stop():
    """Stops the server"""
    print("stopping server ")


@server_at.actions.wrap
def config():
    """Manage the server configuration"""
    pass


class Config(BaseModel):
    name: str = Field(description="The name of the server")


@config.actions.wrap
def set(config: Config):
    """Set the server configuration"""
    print(config)


@config.actions.wrap
def get():
    """Get the server configuration"""
    print("config get")


def main():
    if not main_at.run_args(sys.argv).success:
        sys.exit(1)


if __name__ == "__main__":
    main()
