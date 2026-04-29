from datetime import datetime

from lythonic import GlobalRef, utc_now
from lythonic.compose.namespace import Namespace, NsCacheConfig, TriggerConfig
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
    stamp: datetime = Field(default_factory=utc_now)


def pydantic_hello(input: HelloIn) -> HelloOut:
    """Reverses name and negates age.

    Demonstrates BaseModel input/output round-trip.
    Given a HelloIn with name and age, returns HelloOut
    with the name reversed and age negated.
    """
    return HelloOut(eman=input.name[::-1], ega=-input.age)


def cached_hello(name: str, age: int) -> HelloOut:
    return pydantic_hello(HelloIn(name=name, age=age))


ns = Namespace()
ns.register_all(hello, pydantic_hello, tags=["api"])

ns.register(
    cached_hello,
    config=NsCacheConfig(
        nsref=str(GlobalRef(cached_hello)),
        tags=["api"],
        min_ttl=0.5,
        max_ttl=1.0,
        triggers=[
            TriggerConfig(
                schedule="59 * * * *",
                name="hourly_cached_hello",
                type="poll",
                payload={"name": "jon", "age": 20},
            )
        ],
    ),
)
