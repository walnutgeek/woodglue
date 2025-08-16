import sys

from pydantic import BaseModel, Field

from woodglue.annotated import ActionTree, RunContext, nop

main_at = ActionTree(nop)


class Server(BaseModel):
    """
    Managing the server:
    """

    data: str = Field(default="./data")


server_at = main_at.actions.add(Server)


@server_at.actions.wrap
def start(ctx: RunContext):
    print(f"Starting server with data: {ctx.get('/server')}")


@server_at.actions.wrap
def stop():
    print("stopping server ")


@server_at.actions.wrap
def config():
    pass


class Config(BaseModel):
    name: str = Field(description="The name of the server")


@config.actions.wrap
def set(config: Config):
    print(config)


@config.actions.wrap
def get():
    print("config get")


def main():
    main_at.run_args(sys.argv[1:])


if __name__ == "__main__":
    main()
