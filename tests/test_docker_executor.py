import subprocess
from pathlib import Path

import pytest

from control_plane.domain import SandboxError
from control_plane.executors.docker import DockerSandboxExecutor, DockerSandboxPolicy
from control_plane.tools import ToolName, ToolRequest


def test_policy_requires_digest_pinning() -> None:
    with pytest.raises(SandboxError, match="pinned"):
        DockerSandboxPolicy(image="python:3.11-alpine").validate()


def test_policy_denies_network_and_root() -> None:
    with pytest.raises(SandboxError, match="network"):
        DockerSandboxPolicy(network="bridge").validate()
    with pytest.raises(SandboxError, match="root"):
        DockerSandboxPolicy(user="0:0").validate()


def test_write_requires_task_scoped_path(tmp_path: Path) -> None:
    executor = DockerSandboxExecutor(DockerSandboxPolicy(writable_paths=("src",)))
    request = ToolRequest(name=ToolName.WRITE_TEXT_FILE, path="docs/out.txt", content="no")
    with pytest.raises(SandboxError, match="outside the task grant"):
        executor.execute(workspace=tmp_path, request=request)


def test_timeout_terminates_only_the_scoped_container(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = DockerSandboxExecutor(DockerSandboxPolicy(timeout_seconds=1))
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        calls.append(command)
        if "run" in command:
            raise subprocess.TimeoutExpired(command, 1)
        return subprocess.CompletedProcess(command, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SandboxError, match="timeout"):
        executor.execute(
            workspace=tmp_path,
            request=ToolRequest(name=ToolName.LIST_FILES),
        )
    assert len(calls) == 3
    container_name = calls[0][calls[0].index("--name") + 1]
    assert calls[1][-2:] == ["kill", container_name]
    assert calls[2][-3:] == ["rm", "-f", container_name]


def test_container_invocation_contains_security_controls(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executor = DockerSandboxExecutor()
    captured: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[bytes]:
        captured.extend(command)
        return subprocess.CompletedProcess(command, 0, b"ok", b"")

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = executor.execute(
        workspace=tmp_path,
        request=ToolRequest(name=ToolName.LIST_FILES),
    )
    assert result.status == "SUCCEEDED"
    assert captured[captured.index("--network") + 1] == "none"
    assert captured[captured.index("--cap-drop") + 1] == "ALL"
    assert captured[captured.index("--security-opt") + 1] == "no-new-privileges:true"
    assert "--read-only" in captured
    assert "--pids-limit" in captured
    assert "/var/run/docker.sock" not in " ".join(captured)


@pytest.mark.docker
def test_hardened_container_runs_allowlisted_tools(tmp_path: Path) -> None:
    source = tmp_path / "src"
    source.mkdir()
    (source / "valid.py").write_text("value = 1\n", encoding="utf-8")
    executor = DockerSandboxExecutor(DockerSandboxPolicy(writable_paths=("src",)))

    listing = executor.execute(
        workspace=tmp_path,
        request=ToolRequest(name=ToolName.LIST_FILES),
    )
    assert listing.status == "SUCCEEDED"
    assert "src/valid.py" in listing.stdout
    assert listing.network == "none"
    assert listing.read_only_root is True

    compile_result = executor.execute(
        workspace=tmp_path,
        request=ToolRequest(name=ToolName.PYTHON_COMPILE, targets=("src",)),
    )
    assert compile_result.status == "SUCCEEDED"

    write_result = executor.execute(
        workspace=tmp_path,
        request=ToolRequest(
            name=ToolName.WRITE_TEXT_FILE,
            path="src/generated.py",
            content="generated = True\n",
        ),
    )
    assert write_result.status == "SUCCEEDED"
    assert (source / "generated.py").read_text(encoding="utf-8") == "generated = True\n"

    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_generated.py").write_text(
        "import unittest\n\n"
        "class GeneratedTest(unittest.TestCase):\n"
        "    def test_value(self):\n"
        "        self.assertEqual(1 + 1, 2)\n",
        encoding="utf-8",
    )
    unittest_result = executor.execute(
        workspace=tmp_path,
        request=ToolRequest(
            name=ToolName.PYTHON_UNITTEST,
            targets=("tests/test_generated.py",),
        ),
    )
    assert unittest_result.status == "SUCCEEDED"
    assert "OK" in unittest_result.stderr
