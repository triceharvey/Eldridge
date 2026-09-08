from __future__ import annotations

from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ToolName(StrEnum):
    LIST_FILES = "LIST_FILES"
    PYTHON_COMPILE = "PYTHON_COMPILE"
    WRITE_TEXT_FILE = "WRITE_TEXT_FILE"


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: ToolName
    path: str | None = Field(default=None, max_length=500)
    content: str | None = Field(default=None, max_length=65_536)
    targets: tuple[str, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def validate_shape_and_paths(self) -> ToolRequest:
        if self.name == ToolName.WRITE_TEXT_FILE:
            if self.path is None or self.content is None:
                raise ValueError("WRITE_TEXT_FILE requires path and content")
        elif self.path is not None or self.content is not None:
            raise ValueError(f"{self.name.value} does not accept path or content")
        for value in ((self.path,) if self.path is not None else ()) + self.targets:
            validate_relative_path(value)
        return self


def validate_relative_path(value: str) -> PurePosixPath:
    if not value or "\x00" in value or "\\" in value:
        raise ValueError("tool paths must be non-empty POSIX relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("absolute paths and parent traversal are forbidden")
    if any(part in {"", ".git"} for part in path.parts):
        raise ValueError("empty and .git path components are forbidden")
    return path
