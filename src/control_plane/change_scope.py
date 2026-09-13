"""Classify changed repository paths into coarse change-scope buckets."""

from collections.abc import Iterable


def classify_changed_paths(paths: Iterable[str]) -> dict[str, tuple[str, ...]]:
    """Classify POSIX-relative paths into source, tests, docs, and other buckets.

    Each path is validated before classification. Invalid paths raise ``ValueError``.
    The returned dictionary always contains exactly the keys ``source``, ``tests``,
    ``docs``, and ``other``, each mapped to a sorted tuple of unique validated paths.
    """
    buckets: dict[str, set[str]] = {
        "source": set(),
        "tests": set(),
        "docs": set(),
        "other": set(),
    }
    for path in paths:
        _validate_path(path)
        top = path.split("/", 1)[0]
        if top == "src":
            buckets["source"].add(path)
        elif top == "tests":
            buckets["tests"].add(path)
        elif top == "docs":
            buckets["docs"].add(path)
        else:
            buckets["other"].add(path)
    return {key: tuple(sorted(value)) for key, value in buckets.items()}


def _validate_path(path: str) -> None:
    if not path:
        raise ValueError("path must not be empty")
    if "\x00" in path:
        raise ValueError(f"path must not contain a NUL character: {path!r}")
    if "\\" in path:
        raise ValueError(f"path must not contain a backslash: {path!r}")
    if path.startswith("/"):
        raise ValueError(f"path must not be absolute: {path!r}")
    for component in path.split("/"):
        if component == "..":
            raise ValueError(f"path must not contain parent traversal: {path!r}")
        if component == ".":
            raise ValueError(f"path must not contain a dot component: {path!r}")
        if component == ".git":
            raise ValueError(f"path must not contain a .git component: {path!r}")
