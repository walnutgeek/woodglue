import sys
from pathlib import Path

from lythonic import GlobalRef
from lythonic.compose.cli import ActionTree, Main, RunContext
from lythonic.compose.namespace import Namespace
from pydantic import BaseModel, Field

from woodglue.config import load_config

main_at = ActionTree(Main)


class Server(BaseModel):
    """Managing the server"""

    data: Path = Field(default=Path("./data"), description="directory to store all server data")
    port: int = Field(default=5321, description="port to listen on")
    host: str = Field(default="127.0.0.1", description="host to bind to")


server_at = main_at.actions.add(Server)


def load_namespaces(ns_map: dict[str, str]) -> dict[str, Namespace]:
    """Load all namespaces from config, keyed by prefix."""
    result: dict[str, Namespace] = {}
    for prefix, module_path in ns_map.items():
        gref = GlobalRef(module_path)
        ns = gref.get_instance()
        assert isinstance(ns, Namespace), f"{module_path} is not a Namespace"
        result[prefix] = ns
    return result


@server_at.actions.wrap
def start(ctx: RunContext):  # pyright: ignore[reportUnusedParameter]
    """Starts the server in the foreground"""
    import tornado.ioloop

    from woodglue.apps.server import create_app

    server_cfg = ctx.path.get("/server")
    assert isinstance(server_cfg, Server)

    config = load_config(server_cfg.data)
    namespaces = load_namespaces(config.namespaces)

    app = create_app(namespaces=namespaces, config=config)
    app.listen(server_cfg.port, server_cfg.host)
    print(f"Woodglue server listening on http://{server_cfg.host}:{server_cfg.port}")
    print(f"  RPC endpoint: http://{server_cfg.host}:{server_cfg.port}/rpc")
    if config.docs.enabled:
        print(f"  LLM docs:     http://{server_cfg.host}:{server_cfg.port}/docs/llms.txt")
    if config.ui.enabled:
        print(f"  UI:           http://{server_cfg.host}:{server_cfg.port}/ui/")
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
