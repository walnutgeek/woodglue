from lythonic.compose.namespace import Namespace
from pydantic import BaseModel


def hello(name: str) -> int:
    return len(name)


class HelloIn(BaseModel):
    name: str
    age: int


class HelloOut(BaseModel):
    eman: str
    ega: int


def pydantic_hello(input: HelloIn) -> HelloOut:
    return HelloOut(eman=input.name[::-1], ega=-input.age)


ns = Namespace()
ns.register_all(hello, pydantic_hello)
