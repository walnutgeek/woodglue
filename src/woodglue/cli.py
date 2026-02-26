import sys
from ctypes import cast
from pathlib import Path

from pydantic import BaseModel, Field

from lythonic.compose.cli import ActionTree, Main, RunContext

main_at = ActionTree(Main)


class Server(BaseModel):
    """Managing the server"""

    data: Path = Field(default=Path("./data"), description="directory to store all server data")


server_at = main_at.actions.add(Server)


@server_at.actions.wrap
def start(ctx: RunContext):
    """Starts the server in the foreground"""

    server: Server = cast(Server, ctx.path.get("/server"))
    if server.data.is_dir():
        config_dir = server.data / "config"
        # if config_dir.is_dir():
        #     config = load_config(config_dir)

    print(f"Starting server with data: {ctx.path.get('/server')}")


@server_at.actions.wrap
def stop():
    """Stops the server"""
    print("stopping server ")


def main():
    if not main_at.run_args(sys.argv).success:
        sys.exit(1)


if __name__ == "__main__":
    main()
