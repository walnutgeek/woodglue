from lythonic.compose.namespace import Namespace
from pydantic import BaseModel, Field


def hello(name: str) -> int:
    """Returns the length of a name.

    A simple example method that counts characters.
    """
    return len(name)


class HelloIn(BaseModel):
    name: str
    age: int


class HelloOut(BaseModel):
    eman: str = Field(default="enon", description="inversed name")
    ega: int


def pydantic_hello(input: HelloIn) -> HelloOut:
    """Reverses name and negates age.

    Demonstrates BaseModel input/output round-trip.
    Given a HelloIn with name and age, returns HelloOut
    with the name reversed and age negated.
    """
    return HelloOut(eman=input.name[::-1], ega=-input.age)


ns = Namespace()
ns.register_all(hello, pydantic_hello)
