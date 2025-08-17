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
    ctx.print(f"Starting server with data directory: {ctx.path.get('/server').data}")  # pyright: ignore[reportOptionalMemberAccess]


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


def test_main():
    def run(cmd: str, success: bool, match_output: list[str]):
        rr = main_at.run_args(cmd.split(), print_func=None)
        assert rr.success == success

        def x(oo: list[str]) -> tuple[str, ...]:
            return tuple(map(lambda x: x.strip(), oo))

        match = x(rr.msgs) == x(match_output)
        if not match:
            print(repr(rr.msgs))
        return match

    path_type = type(Path("/")).__name__
    full_help = [
        "Usage: ",
        "  wgl ",
        "    [--help] - bool: Show help. Default: False",
        "    Actions:",
        "      server - Managing the server",
        f"          [--data=value] - Path: directory to store all server data. Default: {path_type}('data')",
        "          Actions:",
        "            start - Starts the server in the foreground",
        "            stop - Stops the server",
        "            config - Manage the server configuration",
        "                Actions:",
        "                  set - Set the server configuration",
        "                      <config> - Config: ",
        "                  get - Get the server configuration",
    ]
    assert run("wgl -help", False, ["Error: Argument '-help' is not a valid", *full_help])
    assert run(
        "wgl --help",
        False,
        ["Error: Action need to be specified, expected one of server", *full_help],
    )
    assert run(
        "wgl --help=y",
        False,
        ["Error: Action need to be specified, expected one of server", *full_help],
    )
    assert run("wgl --help=y --help", False, ["Error: --help is already set to True", *full_help])
    assert run(
        "wgl server",
        False,
        [
            "Error: Action need to be specified, expected one of start, stop, config",
            "Usage: ",
            "  wgl server",
            *full_help[5:],
        ],
    )
    assert run("wgl server start", True, ["Starting server with data directory: data"])
    assert run("wgl server stop", True, [])
    assert run(
        "wgl server config",
        False,
        [
            "Error: Action need to be specified, expected one of set, get",
            "Usage: ",
            "  wgl server config",
            *full_help[10:],
        ],
    )
    assert run(
        "wgl server config set",
        False,
        [
            "Error: Required arguments are missing: <config>",
            "Usage: ",
            "  wgl server config set",
            "    <config> - Config: ",
        ],
    )
    assert run("wgl server config get", True, [])
    assert run(
        "wgl server config set {}",
        False,
        [
            "Error: 1 validation error for Config\nname\n  Field required [type=missing, input_value={}, input_type=dict]\n    For further information visit https://errors.pydantic.dev/2.11/v/missing",
            "Usage: ",
            "  wgl server config set",
            "    <config> - Config: ",
        ],
    )
    assert run('wgl server config set {"name":"test"}', True, [])
