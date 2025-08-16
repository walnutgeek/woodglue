import inspect
from collections.abc import Callable
from pathlib import Path
from typing import Any, Generic, NamedTuple, TypeVar

from pydantic import BaseModel

from woodglue import GlobalRef


class ArgInfo(NamedTuple):
    name: str
    annotation: Any | None
    default: Any | None
    is_optional: bool

    @classmethod
    def from_param(cls, param: inspect.Parameter):
        return cls(
            name=param.name,
            annotation=param.annotation if param.annotation != inspect.Parameter.empty else None,
            default=param.default if param.default != inspect.Parameter.empty else None,
            is_optional=param.default != inspect.Parameter.empty,
        )

    def to_value(self, v: str):
        if self.annotation is None:
            return v
        if self.annotation is bool:
            return v.lower() in ("true", "1", "yes", "y")
        if issubclass(self.annotation, BaseModel):
            return self.annotation.model_validate_json(v)
        return self.annotation(v)


class Method:
    gref: GlobalRef
    _o: Callable[..., Any] | None
    _args: list[ArgInfo] | None
    _args_by_name: dict[str, ArgInfo] | None
    _returns: Any | None

    def __init__(self, o: Callable[..., Any] | GlobalRef):
        if isinstance(o, GlobalRef):
            self.gref = o
            self._o = None
        else:
            self.gref = GlobalRef(o)
            assert isinstance(o, Callable), "method instance must be a callable"
            self._o = o
        self._args = None
        self._args_by_name = None
        self._returns = None

    def _update_from_signature(self):
        o = self.o
        sig = inspect.signature(o)
        self._args = [ArgInfo.from_param(param) for param in sig.parameters.values()]
        self._args_by_name = {arg.name: arg for arg in self._args}
        self._returns = sig.return_annotation

    @property
    def o(self) -> Callable[..., Any]:
        if self._o is None:
            self._o = self.gref.get_instance()
        assert self._o is not None
        return self._o

    @property
    def args(self) -> list[ArgInfo]:
        if self._args is None:
            self._update_from_signature()
        assert self._args is not None
        return self._args

    @property
    def args_by_name(self) -> dict[str, ArgInfo]:
        if self._args_by_name is None:
            self._update_from_signature()
        assert self._args_by_name is not None
        return self._args_by_name

    @property
    def returns(self) -> Any | None:
        if self._args is None:
            self._update_from_signature()
        return self._returns

    @property
    def name(self):
        return self.gref.name

    @property
    def doc(self):
        return self.o.__doc__

    def __call__(self, *args: Any, **kwargs: Any):
        return self.o(*args, **kwargs)


def nop():
    pass


NOP = Method(nop)


T = TypeVar("T", bound=Method)


class MethodDict(Generic[T], dict[str, T]):
    method_type: type[T]

    def __init__(self, method_type: type[T]):
        super().__init__()
        self.method_type = method_type

    def add(self, o: Callable[..., Any]) -> T:
        m = self.method_type(o)
        self[m.name.lower()] = m
        return m

    def wrap(self, o: Callable[..., Any]) -> T:
        return self.add(o)


class RunContext:
    state_by_path: dict[Path, Any]

    def __init__(self):
        self.state_by_path = {}

    def get(self, path: Path | str) -> Any:
        if isinstance(path, str):
            path = Path(path)
        return self.state_by_path[path]


class ActionTree(Method):
    actions: MethodDict["ActionTree"]

    def __init__(self, o: Callable[..., Any] | GlobalRef):
        super().__init__(o)
        self.actions = MethodDict["ActionTree"](method_type=self.__class__)

    def run_args(
        self, cli_args: list[str], ctx: RunContext | None = None, path: Path | None = None
    ):
        ctx = ctx or RunContext()
        path = path or Path("/")
        current_arg_index = 0
        arg_values = {}

        required_args = self.args
        if len(self.args) and self.args[0].name == "ctx" and self.args[0].annotation == RunContext:
            arg_values["ctx"] = ctx
            required_args = required_args[1:]
        required_args = [arg for arg in required_args if not arg.is_optional]

        while current_arg_index < len(cli_args):
            arg_str = cli_args[current_arg_index]
            if required_args:
                arg = required_args[0]
                if arg_str.startswith("--"):
                    raise ValueError(f"Argument {arg.name} is required but getting {arg_str}")
                current_arg_index += 1
                arg_values[arg.name] = arg.to_value(arg_str)
                required_args.pop(0)
                continue
            if arg_str.startswith("--"):
                k, v = arg_str[2:].split("=", 1)
                if k in arg_values:
                    raise ValueError(f"Argument {k} is already set to {arg_values[k]}")
                if k not in self.args_by_name:
                    raise ValueError(
                        f"Argument {k} is not a valid argument, expected one of {', '.join(self.args_by_name.keys())}"
                    )
                arg = self.args_by_name[k]
                arg_values[k] = arg.to_value(v)
                current_arg_index += 1
                continue
            elif arg_str in self.actions:
                action: ActionTree = self.actions[arg_str]
                ctx.state_by_path[path] = self(**arg_values)
                action.run_args(cli_args[current_arg_index + 1 :], ctx, path / arg_str)
                return
            raise ValueError(f"Argument {arg_str} is not a valid argument")
        if required_args:
            raise ValueError(
                f"Required arguments are missing: {', '.join([arg.name for arg in required_args])}"
            )
        if self.actions:
            raise ValueError(
                f"Action need to be specified, expected one of {', '.join(self.actions.keys())}"
            )
        self(**arg_values)
