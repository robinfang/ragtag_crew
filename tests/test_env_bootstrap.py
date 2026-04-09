from __future__ import annotations

import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from ragtag_crew.config import settings
from ragtag_crew import env_bootstrap as eb_module
from ragtag_crew.env_bootstrap import (
    _build_snapshot,
    _detect_tech_stack,
    _scan_tree,
    load_workspace_snapshot,
)


@contextmanager
def bootstrap_settings(
    enabled: bool = True,
    max_depth: int = 3,
    max_tokens: int = 2000,
    skip_dirs: str = ".git,.venv,__pycache__,node_modules",
    working_dir: str | None = None,
):
    orig_enabled = settings.env_bootstrap_enabled
    orig_depth = settings.env_bootstrap_max_depth
    orig_tokens = settings.env_bootstrap_max_tokens
    orig_skip = settings.env_bootstrap_skip_dirs
    orig_wd = settings.working_dir

    settings.env_bootstrap_enabled = enabled
    settings.env_bootstrap_max_depth = max_depth
    settings.env_bootstrap_max_tokens = max_tokens
    settings.env_bootstrap_skip_dirs = skip_dirs
    if working_dir is not None:
        settings.working_dir = working_dir
    eb_module._cache_timestamp = 0.0
    eb_module._cache_result = ""

    try:
        yield
    finally:
        settings.env_bootstrap_enabled = orig_enabled
        settings.env_bootstrap_max_depth = orig_depth
        settings.env_bootstrap_max_tokens = orig_tokens
        settings.env_bootstrap_skip_dirs = orig_skip
        settings.working_dir = orig_wd
        eb_module._cache_timestamp = 0.0
        eb_module._cache_result = ""


class EnvBootstrapTests(unittest.TestCase):
    def test_load_returns_empty_when_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with bootstrap_settings(enabled=False, working_dir=tmp):
                result = load_workspace_snapshot()
        self.assertEqual(result, "")

    def test_load_returns_empty_for_nonexistent_dir(self) -> None:
        with bootstrap_settings(working_dir="Z:\\nonexistent_dir_xyz"):
            result = load_workspace_snapshot()
        self.assertEqual(result, "")

    def test_scan_tree_produces_indented_structure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "main.py").write_text("# main", encoding="utf-8")
            (root / "README.md").write_text("# hello", encoding="utf-8")

            tree = _scan_tree(root, max_depth=3, skip_dirs=set())

        self.assertIn("src/", tree)
        self.assertIn("main.py", tree)
        self.assertIn("README.md", tree)

    def test_scan_tree_respects_depth(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            deep = root / "a" / "b" / "c" / "d"
            deep.mkdir(parents=True)
            (deep / "file.txt").write_text("x", encoding="utf-8")

            tree = _scan_tree(root, max_depth=1, skip_dirs=set())

        lines = tree.splitlines()
        self.assertTrue(any(line.strip().endswith("a/") for line in lines))
        self.assertFalse(any(line.strip().endswith("b/") for line in lines))

    def test_scan_tree_skips_configured_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / "src").mkdir()

            tree = _scan_tree(root, max_depth=3, skip_dirs={".git"})

        self.assertNotIn(".git", tree)
        self.assertIn("src/", tree)

    def test_scan_tree_hides_dotfiles_except_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("KEY=val", encoding="utf-8")
            (root / ".hidden").write_text("", encoding="utf-8")

            tree = _scan_tree(root, max_depth=3, skip_dirs=set())

        self.assertIn(".env", tree)
        self.assertNotIn(".hidden", tree)

    def test_detect_tech_stack_finds_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "pyproject.toml").write_text(
                "[project]\nname = 'myapp'\nversion = '0.1.0'",
                encoding="utf-8",
            )

            tech = _detect_tech_stack(root)

        self.assertIn("Python", tech)
        self.assertIn("pyproject.toml", tech)
        self.assertIn("myapp", tech)

    def test_detect_tech_stack_returns_empty_for_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tech = _detect_tech_stack(Path(tmp))
        self.assertEqual(tech, "")

    def test_snapshot_respects_token_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for i in range(200):
                (root / f"file_{i:03d}.txt").write_text("x" * 100, encoding="utf-8")

            snapshot = _build_snapshot(root)

        self.assertLessEqual(len(snapshot), 2000 * 4 + 10)

    def test_full_load_produces_tree_and_tech(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "pyproject.toml").write_text(
                "[project]\nname='test'", encoding="utf-8"
            )

            with bootstrap_settings(working_dir=tmp):
                snapshot = load_workspace_snapshot()

        self.assertIn("src/", snapshot)
        self.assertIn("pyproject.toml", snapshot)
        self.assertIn("Python", snapshot)


if __name__ == "__main__":
    unittest.main()
