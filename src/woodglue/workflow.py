from pathlib import Path
from typing import Any, Literal

import yaml
from lythonic import GlobalRef
from lythonic.compose import Method
from pydantic import BaseModel, Field, model_validator

Action = Literal["end_workflow", "skip_next_task"]


class Task(BaseModel):
    task: None | GlobalRef = Field(description="The task to run", default=None)
    if_task: None | GlobalRef = Field(
        description="The task to check if the workflow should run", default=None
    )
    request: dict[str, Any] = Field(description="The request to pass to the task", default={})
    else_: None | list[Action] = Field(
        description="The actions to run if the task fails", alias="else", default=None
    )

    def task_method(self) -> Method:
        t = self.task if self.task is not None else self.if_task
        assert t is not None
        return Method(t)

    @model_validator(mode="after")
    def check_task_fields(self):
        if (self.task is None and self.if_task is None) or (
            self.task is not None and self.if_task is not None
        ):
            raise ValueError("Exactly one of 'task' or 'if_task' must be defined")
        if self.else_ is not None and self.if_task is None:
            raise ValueError("'else' can only be defined when 'if_task' is set")
        return self


class Workflow(BaseModel):
    name: None | str = Field(description="The name of the workflow", default=None)
    frequency: int = Field(description="The frequency to run the workflow in seconds")
    paths: dict[str, Path] = Field(description="The globals to pass to the workflow", default={})
    tasks: list[Task]

    @staticmethod
    def from_yaml_file(file_path: Path | str, data_path: Path) -> "Workflow":
        file_path = Path(file_path)
        with file_path.open("r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if "name" not in data or not data["name"]:
            data["name"] = file_path.stem

        paths = data["paths"]
        for path in paths:
            paths[path] = data_path / data["name"] / paths[path]

        return Workflow(**data)
