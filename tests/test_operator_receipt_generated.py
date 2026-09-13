"""Deterministic unit tests for control_plane.operator_receipt."""

from __future__ import annotations

import copy
import math
import unittest

from control_plane.operator_receipt import digest_operator_preflight

_VALID_BASE_REVISION = "a" * 40
_SHA256_PREFIX = "sha256:"
_SHA256_HEX_LENGTH = 64
_HEX_CHARS = "0123456789abcdef"


def _valid_preflight() -> dict[str, object]:
    return {
        "result": "READY",
        "schema_version": "1",
        "repository_scope": "triceharvey/Eldridge",
        "base_revision": _VALID_BASE_REVISION,
        "provider_policy_version": "policy-v1",
        "stops_at": "AWAITING_HUMAN_APPROVAL",
        "writable_paths": [
            "src/control_plane/operator_receipt.py",
            "tests/test_operator_receipt_generated.py",
        ],
        "providers": {"anthropic": "claude-3", "openai": "gpt-4"},
    }


class DigestOperatorPreflightTests(unittest.TestCase):
    def test_valid_preflight_returns_sha256_digest(self) -> None:
        digest = digest_operator_preflight(_valid_preflight())
        self.assertTrue(digest.startswith(_SHA256_PREFIX))
        hex_part = digest.removeprefix(_SHA256_PREFIX)
        self.assertEqual(len(hex_part), _SHA256_HEX_LENGTH)
        self.assertTrue(all(char in _HEX_CHARS for char in hex_part))

    def test_digest_is_stable_across_mapping_key_order(self) -> None:
        preflight = _valid_preflight()
        reordered = dict(reversed(list(preflight.items())))
        self.assertEqual(
            digest_operator_preflight(preflight),
            digest_operator_preflight(reordered),
        )

    def test_digest_is_deterministic(self) -> None:
        preflight = _valid_preflight()
        self.assertEqual(
            digest_operator_preflight(preflight),
            digest_operator_preflight(copy.deepcopy(preflight)),
        )

    def test_digest_changes_when_base_revision_changes(self) -> None:
        baseline = digest_operator_preflight(_valid_preflight())
        changed = _valid_preflight()
        changed["base_revision"] = "b" * 40
        self.assertNotEqual(baseline, digest_operator_preflight(changed))

    def test_digest_changes_when_providers_change(self) -> None:
        baseline = digest_operator_preflight(_valid_preflight())
        changed = _valid_preflight()
        changed["providers"] = {"anthropic": "claude-3-opus", "openai": "gpt-4"}
        self.assertNotEqual(baseline, digest_operator_preflight(changed))

    def test_digest_changes_when_writable_paths_change(self) -> None:
        baseline = digest_operator_preflight(_valid_preflight())
        changed = _valid_preflight()
        changed["writable_paths"] = ["only.py"]
        self.assertNotEqual(baseline, digest_operator_preflight(changed))

    def test_missing_result_is_rejected(self) -> None:
        preflight = _valid_preflight()
        del preflight["result"]
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_wrong_result_value_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["result"] = "PENDING"
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_non_string_result_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["result"] = True
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_schema_version_string_one_is_accepted(self) -> None:
        preflight = _valid_preflight()
        preflight["schema_version"] = "1"
        digest_operator_preflight(preflight)

    def test_schema_version_integer_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["schema_version"] = 1
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_schema_version_boolean_true_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["schema_version"] = True
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_schema_version_boolean_false_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["schema_version"] = False
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_schema_version_wrong_string_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["schema_version"] = "2"
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_missing_schema_version_is_rejected(self) -> None:
        preflight = _valid_preflight()
        del preflight["schema_version"]
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_empty_repository_scope_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["repository_scope"] = ""
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_non_string_repository_scope_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["repository_scope"] = 123
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_base_revision_wrong_length_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["base_revision"] = "a" * 39
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_base_revision_uppercase_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["base_revision"] = "A" * 40
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_base_revision_non_hex_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["base_revision"] = "g" * 40
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_missing_provider_policy_version_is_rejected(self) -> None:
        preflight = _valid_preflight()
        del preflight["provider_policy_version"]
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_empty_provider_policy_version_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["provider_policy_version"] = ""
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_wrong_stops_at_value_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["stops_at"] = "DONE"
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_writable_paths_not_a_list_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["writable_paths"] = ("a.py", "b.py")
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_writable_paths_empty_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["writable_paths"] = []
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_writable_paths_duplicate_entries_are_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["writable_paths"] = ["a.py", "a.py"]
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_writable_paths_empty_entry_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["writable_paths"] = ["a.py", ""]
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_writable_paths_non_string_entry_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["writable_paths"] = ["a.py", 1]
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_providers_not_a_mapping_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["providers"] = ["anthropic"]
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_providers_empty_mapping_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["providers"] = {}
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_providers_empty_key_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["providers"] = {"": "claude-3"}
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_providers_empty_value_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["providers"] = {"anthropic": ""}
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_providers_non_string_value_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["providers"] = {"anthropic": 1}
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_nan_value_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["extra"] = math.nan
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_infinity_value_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["extra"] = math.inf
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)

    def test_unserializable_value_is_rejected(self) -> None:
        preflight = _valid_preflight()
        preflight["extra"] = {1, 2, 3}
        with self.assertRaises(ValueError):
            digest_operator_preflight(preflight)


if __name__ == "__main__":
    unittest.main()
