"""Digest computation for Eldridge operator preflight receipts.

This module validates an operator preflight payload against the
Eldridge control-plane contract and produces a stable, deterministic
digest of the payload suitable for use as a receipt identifier.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

_REQUIRED_RESULT = "READY"
_REQUIRED_SCHEMA_VERSION = "1"
_REQUIRED_STOPS_AT = "AWAITING_HUMAN_APPROVAL"
_HEX_DIGITS = frozenset("0123456789abcdef")
_BASE_REVISION_LENGTH = 40


def _require_non_bool_str(value: object, field: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    return value


def _require_nonempty_str(value: object, field: str) -> str:
    text = _require_non_bool_str(value, field)
    if text == "":
        raise ValueError(f"{field} must be a nonempty string")
    return text


def _validate_result(preflight: Mapping[str, object]) -> None:
    value = preflight.get("result")
    text = _require_non_bool_str(value, "result")
    if text != _REQUIRED_RESULT:
        raise ValueError("result must equal 'READY'")


def _validate_schema_version(preflight: Mapping[str, object]) -> None:
    value = preflight.get("schema_version")
    text = _require_non_bool_str(value, "schema_version")
    if text != _REQUIRED_SCHEMA_VERSION:
        raise ValueError("schema_version must equal '1'")


def _validate_repository_scope(preflight: Mapping[str, object]) -> None:
    value = preflight.get("repository_scope")
    _require_nonempty_str(value, "repository_scope")


def _validate_base_revision(preflight: Mapping[str, object]) -> None:
    value = preflight.get("base_revision")
    text = _require_non_bool_str(value, "base_revision")
    if len(text) != _BASE_REVISION_LENGTH or not set(text) <= _HEX_DIGITS:
        raise ValueError("base_revision must be 40 lowercase hex characters")


def _validate_provider_policy_version(preflight: Mapping[str, object]) -> None:
    value = preflight.get("provider_policy_version")
    _require_nonempty_str(value, "provider_policy_version")


def _validate_stops_at(preflight: Mapping[str, object]) -> None:
    value = preflight.get("stops_at")
    text = _require_non_bool_str(value, "stops_at")
    if text != _REQUIRED_STOPS_AT:
        raise ValueError("stops_at must equal 'AWAITING_HUMAN_APPROVAL'")


def _validate_writable_paths(preflight: Mapping[str, object]) -> None:
    value = preflight.get("writable_paths")
    if not isinstance(value, list):
        raise ValueError("writable_paths must be a list")
    if len(value) == 0:
        raise ValueError("writable_paths must be nonempty")
    seen: set[str] = set()
    for entry in value:
        text = _require_nonempty_str(entry, "writable_paths entry")
        if text in seen:
            raise ValueError("writable_paths must not contain duplicates")
        seen.add(text)


def _validate_providers(preflight: Mapping[str, object]) -> None:
    value = preflight.get("providers")
    if not isinstance(value, Mapping):
        raise ValueError("providers must be a mapping")
    if len(value) == 0:
        raise ValueError("providers must be nonempty")
    for key, provider_value in value.items():
        _require_nonempty_str(key, "providers key")
        _require_nonempty_str(provider_value, "providers value")


def _validate_preflight(preflight: Mapping[str, object]) -> None:
    _validate_result(preflight)
    _validate_schema_version(preflight)
    _validate_repository_scope(preflight)
    _validate_base_revision(preflight)
    _validate_provider_policy_version(preflight)
    _validate_stops_at(preflight)
    _validate_writable_paths(preflight)
    _validate_providers(preflight)


def _canonicalize(preflight: Mapping[str, object]) -> bytes:
    try:
        text = json.dumps(
            dict(preflight),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except ValueError as exc:
        raise ValueError("preflight contains non-finite numeric values") from exc
    except TypeError as exc:
        raise ValueError("preflight is not JSON-serializable") from exc
    return text.encode("utf-8")


def digest_operator_preflight(preflight: Mapping[str, object]) -> str:
    """Validate an operator preflight payload and return its receipt digest.

    The digest is a lowercase ``sha256:``-prefixed hex digest of the
    canonical UTF-8 JSON encoding of ``preflight``. Canonical encoding
    uses sorted keys, compact separators, ASCII escaping, and rejects
    non-finite numbers.

    Raises:
        ValueError: If a required field is missing or malformed, or if
            the payload cannot be serialized as canonical JSON.
    """
    _validate_preflight(preflight)
    digest = hashlib.sha256(_canonicalize(preflight)).hexdigest()
    return f"sha256:{digest}"
