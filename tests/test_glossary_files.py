"""glossary_files.py: comment-preserving writes and best-effort git commits
for the series glossaries the web UI edits. Real gap (2026-09-28): one UI
promotion's yaml.safe_dump() rewrite deleted ~50 lines of evidence comments
from the live love-is-in-the-air.yaml, and the edit sat uncommitted for six
days in the glossary's own git repo."""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml
from fastapi.testclient import TestClient

import api
import glossary_files

# The live file's real shape: evidence comments between list entries.
COMMENTED_YAML = """tvdb_id: 111
title: "Test Series"
entities:
- canonical: Eda
  protected: true
# Sirius: confirmed real name-call -- bare "Sirius!" hallucinated to
# "Sirius, what are you doing?".
- canonical: Sirius
  protected: true
# Deliberately NOT added: Ayfer (recurring, but no confirmed bug).
- canonical: Ceren
  protected: true
"""

HAS_GIT = shutil.which("git") is not None


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          check=True, capture_output=True, text=True).stdout


class RoundTripTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "series.yaml"
        self.path.write_text(COMMENTED_YAML, encoding="utf-8")

    def test_append_keeps_every_comment_and_the_rest_byte_identical(self):
        data = glossary_files.load(self.path)
        data["entities"].append({"canonical": "Melek", "aliases": [], "protected": True})
        glossary_files.write(self.path, data)
        text = self.path.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(COMMENTED_YAML), text)
        self.assertIn("- canonical: Melek", text)
        self.assertEqual(yaml.safe_load(text)["entities"][-1]["canonical"], "Melek")

    def test_unchanged_round_trip_is_byte_identical(self):
        glossary_files.write(self.path, glossary_files.load(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), COMMENTED_YAML)

    def test_edit_in_place_keeps_comments(self):
        data = glossary_files.load(self.path)
        data["entities"][1]["aliases"] = ["Sirius Bey"]
        glossary_files.write(self.path, data)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# Sirius: confirmed real name-call", text)
        self.assertIn("# Deliberately NOT added: Ayfer", text)

    def test_indented_list_style_is_kept(self):
        # _turkish.yaml's real style: list items indented under their key.
        indented = 'phrases:\n  - source: "Peki."\n    translation: "Okay."\n'
        self.path.write_text(indented, encoding="utf-8")
        glossary_files.write(self.path, glossary_files.load(self.path))
        self.assertEqual(self.path.read_text(encoding="utf-8"), indented)

    def test_unicode_is_written_literally(self):
        data = glossary_files.load(self.path)
        data["entities"].append({"canonical": "Pırıl", "protected": True})
        glossary_files.write(self.path, data)
        self.assertIn("Pırıl", self.path.read_text(encoding="utf-8"))


@unittest.skipUnless(HAS_GIT, "git not installed")
class CommitTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Path(tmp.name)
        _git(self.repo, "init", "-q")
        self.path = self.repo / "series.yaml"
        self.path.write_text(COMMENTED_YAML, encoding="utf-8")
        (self.repo / "other.yaml").write_text("a: 1\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-q", "-m", "init")

    def test_commits_only_the_edited_file(self):
        self.path.write_text(COMMENTED_YAML + "- canonical: Melek\n  protected: true\n", encoding="utf-8")
        (self.repo / "other.yaml").write_text("a: 2\n", encoding="utf-8")  # unrelated dirty file
        self.assertTrue(glossary_files.commit(self.path, "Protect 'Melek' via web UI"))
        self.assertEqual(_git(self.repo, "log", "-1", "--format=%s|%an").strip(),
                         "Protect 'Melek' via web UI|subtitle-ai web UI")
        self.assertIn("other.yaml", _git(self.repo, "status", "--short"))
        self.assertNotIn("series.yaml", _git(self.repo, "status", "--short"))

    def test_new_untracked_file_is_added(self):
        new = self.repo / "222.yaml"
        new.write_text("tvdb_id: 222\n", encoding="utf-8")
        self.assertTrue(glossary_files.commit(new, "Protect 'Ada' via web UI"))
        self.assertIn("222.yaml", _git(self.repo, "show", "--name-only", "--format="))

    def test_no_change_makes_no_commit(self):
        before = _git(self.repo, "rev-parse", "HEAD")
        self.assertFalse(glossary_files.commit(self.path, "noop"))
        self.assertEqual(_git(self.repo, "rev-parse", "HEAD"), before)


class CommitNeverRaisesTests(unittest.TestCase):
    def test_not_a_repo_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.yaml"
            path.write_text("a: 1\n", encoding="utf-8")
            self.assertFalse(glossary_files.commit(path, "m"))

    def test_no_git_binary_returns_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()
            path = Path(tmp) / "x.yaml"
            path.write_text("a: 1\n", encoding="utf-8")
            with patch("glossary_files.shutil.which", return_value=None):
                self.assertFalse(glossary_files.commit(path, "m"))

    def test_git_failure_is_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / ".git").mkdir()  # looks like a repo, isn't one: git errors
            path = Path(tmp) / "x.yaml"
            path.write_text("a: 1\n", encoding="utf-8")
            self.assertFalse(glossary_files.commit(path, "m"))


@unittest.skipUnless(HAS_GIT, "git not installed")
class ApiIntegrationTests(unittest.TestCase):
    """The endpoints themselves: comments survive and each edit is a commit."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.glossary_dir = Path(tmp.name) / "glossary"
        self.glossary_dir.mkdir()
        self.path = self.glossary_dir / "test-series.yaml"
        self.path.write_text(COMMENTED_YAML, encoding="utf-8")
        _git(self.glossary_dir, "init", "-q")
        _git(self.glossary_dir, "add", ".")
        _git(self.glossary_dir, "commit", "-q", "-m", "init")
        app = api.create_app(Path(tmp.name) / "jobs.db", glossary_dir=str(self.glossary_dir),
                             glossary_suggestions_dir=str(Path(tmp.name) / "suggestions"))
        self.client = TestClient(app)

    def _subjects(self):
        return _git(self.glossary_dir, "log", "--format=%s").splitlines()

    def test_promote_update_delete_each_commit_and_keep_comments(self):
        self.assertEqual(self.client.post("/api/series/111/glossary/promote",
                                          json={"canonical": "Melek"}).status_code, 200)
        self.assertEqual(self.client.post("/api/series/111/glossary/update",
                                          json={"original_canonical": "Melek", "canonical": "Melek",
                                                "aliases": ["Melek Hanım"]}).status_code, 200)
        self.assertEqual(self.client.post("/api/series/111/glossary/delete",
                                          json={"canonical": "Ceren"}).status_code, 200)
        self.assertEqual(self._subjects()[:3], [
            "Unprotect 'Ceren' (series 111) via web UI",
            "Edit 'Melek' (series 111) via web UI",
            "Protect 'Melek' (series 111) via web UI",
        ])
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# Sirius: confirmed real name-call", text)
        self.assertIn("Melek Hanım", text)
        self.assertEqual(_git(self.glossary_dir, "status", "--short"), "")


if __name__ == "__main__":
    unittest.main()
