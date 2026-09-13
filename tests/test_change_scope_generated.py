"""Deterministic unittest coverage for control_plane.change_scope."""

import unittest

from control_plane.change_scope import classify_changed_paths


class ClassifyChangedPathsNormalCasesTests(unittest.TestCase):
    def test_classifies_each_top_level_directory_and_other(self) -> None:
        paths = [
            "src/control_plane/change_scope.py",
            "tests/test_change_scope_generated.py",
            "docs/change_scope.md",
            "README.md",
        ]
        result = classify_changed_paths(paths)
        self.assertEqual(
            result,
            {
                "source": ("src/control_plane/change_scope.py",),
                "tests": ("tests/test_change_scope_generated.py",),
                "docs": ("docs/change_scope.md",),
                "other": ("README.md",),
            },
        )

    def test_returns_empty_tuples_for_unused_buckets(self) -> None:
        result = classify_changed_paths(["src/module.py"])
        self.assertEqual(result["tests"], ())
        self.assertEqual(result["docs"], ())
        self.assertEqual(result["other"], ())

    def test_result_has_exactly_four_keys(self) -> None:
        result = classify_changed_paths(["src/module.py"])
        self.assertEqual(set(result.keys()), {"source", "tests", "docs", "other"})


class ClassifyChangedPathsSortingAndDeduplicationTests(unittest.TestCase):
    def test_sorts_paths_within_each_bucket(self) -> None:
        paths = ["src/z.py", "src/a.py", "src/m.py"]
        result = classify_changed_paths(paths)
        self.assertEqual(result["source"], ("src/a.py", "src/m.py", "src/z.py"))

    def test_deduplicates_repeated_paths(self) -> None:
        paths = ["src/a.py", "src/a.py", "src/a.py"]
        result = classify_changed_paths(paths)
        self.assertEqual(result["source"], ("src/a.py",))

    def test_handles_mixed_buckets_with_duplicates_and_out_of_order_input(self) -> None:
        paths = [
            "docs/b.md",
            "tests/b_test.py",
            "src/b.py",
            "docs/a.md",
            "docs/a.md",
            "other/thing.txt",
            "tests/a_test.py",
            "src/a.py",
        ]
        result = classify_changed_paths(paths)
        self.assertEqual(result["source"], ("src/a.py", "src/b.py"))
        self.assertEqual(result["tests"], ("tests/a_test.py", "tests/b_test.py"))
        self.assertEqual(result["docs"], ("docs/a.md", "docs/b.md"))
        self.assertEqual(result["other"], ("other/thing.txt",))


class ClassifyChangedPathsRejectionTests(unittest.TestCase):
    def test_rejects_empty_path(self) -> None:
        with self.assertRaises(ValueError):
            classify_changed_paths([""])

    def test_rejects_absolute_path(self) -> None:
        with self.assertRaises(ValueError):
            classify_changed_paths(["/src/module.py"])

    def test_rejects_backslash(self) -> None:
        with self.assertRaises(ValueError):
            classify_changed_paths(["src\\module.py"])

    def test_rejects_nul_byte(self) -> None:
        with self.assertRaises(ValueError):
            classify_changed_paths(["src/module\x00.py"])

    def test_rejects_parent_traversal(self) -> None:
        with self.assertRaises(ValueError):
            classify_changed_paths(["src/../module.py"])

    def test_rejects_dot_component(self) -> None:
        with self.assertRaises(ValueError):
            classify_changed_paths(["src/./module.py"])

    def test_rejects_git_component(self) -> None:
        with self.assertRaises(ValueError):
            classify_changed_paths(["src/.git/config"])


if __name__ == "__main__":
    unittest.main()
