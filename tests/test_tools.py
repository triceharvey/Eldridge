import pytest
from pydantic import ValidationError as PydanticValidationError

from control_plane.tools import ToolName, ToolRequest, validate_relative_path


@pytest.mark.parametrize("path", ["../secret", "/etc/passwd", "src/../../secret", ".git/config"])
def test_tool_paths_reject_escape_attempts(path: str) -> None:
    with pytest.raises((ValueError, PydanticValidationError)):
        ToolRequest(name=ToolName.WRITE_TEXT_FILE, path=path, content="blocked")


def test_tool_request_rejects_unknown_fields() -> None:
    with pytest.raises(PydanticValidationError):
        ToolRequest.model_validate({"name": "LIST_FILES", "command": "whoami"})


def test_write_tool_requires_content_and_path() -> None:
    with pytest.raises(PydanticValidationError, match="requires path and content"):
        ToolRequest(name=ToolName.WRITE_TEXT_FILE)


def test_safe_relative_path_is_accepted() -> None:
    assert str(validate_relative_path("src/control_plane/new_file.py")) == (
        "src/control_plane/new_file.py"
    )
