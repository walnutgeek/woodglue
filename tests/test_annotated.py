from woodglue.annotated import Method


def my_func(x, y: bool, z=None, a: int | float = 42, b: str = "foo"):  # pyright: ignore
    pass


def test_method():
    m = Method(my_func)
    print(m.args)
