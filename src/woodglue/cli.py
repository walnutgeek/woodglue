import sys
from pathlib import Path

from lythonic import GlobalRef
from lythonic.compose.cli import ActionTree, Main, RunContext
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel, Field

main_at = ActionTree(Main)


class Server(BaseModel):
    """Managing the server"""

    data: Path = Field(default=Path("./data"), description="directory to store all server data")
    port: int = Field(default=5321, description="port to listen on")
    host: str = Field(default="127.0.0.1", description="host to bind to")
    module_path: str = Field(
        default="", description="Python module path to auto-discover methods from"
    )


server_at = main_at.actions.add(Server)


def load_ns(ns_gref: GlobalRef | str) -> Namespace:
    gref = GlobalRef(ns_gref)
    ns = gref.get_instance()
    assert isinstance(ns, Namespace)
    return ns


@server_at.actions.wrap
def start(ctx: RunContext):  # pyright: ignore[reportUnusedParameter]
    """Starts the server in the foreground"""
    import tornado.ioloop

    from woodglue.apps.server import create_app

    server_cfg = ctx.path.get("/server")
    assert isinstance(server_cfg, Server)
    ns = load_ns(server_cfg.module_path) if server_cfg.module_path else Namespace()
    app = create_app(namespaces={"default": ns})
    app.listen(server_cfg.port, server_cfg.host)
    print(f"Woodglue server listening on http://{server_cfg.host}:{server_cfg.port}")
    print(f"  RPC endpoint: http://{server_cfg.host}:{server_cfg.port}/rpc")
    print(f"  API docs:     http://{server_cfg.host}:{server_cfg.port}/docs/ui")
    tornado.ioloop.IOLoop.current().start()


@server_at.actions.wrap
def stop():
    """Stops the server"""
    print("stopping server")


def main():
    if not main_at.run_args(sys.argv).success:
        sys.exit(1)


if __name__ == "__main__":
    main()
