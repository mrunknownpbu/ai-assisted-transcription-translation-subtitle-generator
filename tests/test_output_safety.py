import tempfile
import unittest
from pathlib import Path

from output import OutputSafetyError, PROTECTED_SUFFIXES, resolve_media_path, resolve_output_path, write_srt_atomic


class ResolveOutputPathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "Show").mkdir()
        (self.root / "Show" / "S01E01.mkv").touch()

    def test_resolves_expected_path(self):
        path = resolve_output_path(self.root, self.root / "Show" / "S01E01.mkv", "en")
        self.assertEqual(path.name, "S01E01.en.srt")

    def test_relative_video_path_resolved_against_root(self):
        path = resolve_output_path(self.root, "Show/S01E01.mkv", "tr")
        self.assertEqual(path.name, "S01E01.tr.srt")

    def test_path_traversal_rejected(self):
        with self.assertRaises(OutputSafetyError):
            resolve_output_path(self.root, "../../etc/passwd", "en")

    def test_absolute_path_outside_root_rejected(self):
        with self.assertRaises(OutputSafetyError):
            resolve_output_path(self.root, "/etc/passwd", "en")

    def test_invalid_language_code_rejected(self):
        for bad in ["EN", "en/../../x", "", "toolongcode", "e1"]:
            with self.subTest(bad=bad):
                with self.assertRaises(OutputSafetyError):
                    resolve_output_path(self.root, self.root / "Show" / "S01E01.mkv", bad)

    def test_cannot_resolve_to_a_protected_suffix(self):
        # "hi" alone is a valid 2-letter code, but the *combination* with
        # a video stem ending ".en" could in principle collide with a
        # protected pattern -- resolve_output_path must catch it even so.
        (self.root / "Show" / "S01E01.en.mkv").touch()
        with self.assertRaises(OutputSafetyError):
            resolve_output_path(self.root, self.root / "Show" / "S01E01.en.mkv", "hi")


class ResolveMediaPathTests(unittest.TestCase):
    """The read-side counterpart to resolve_output_path -- used by the
    GUI's browse/media endpoints, never for writing."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "Show").mkdir()
        (self.root / "Show" / "S01E01.mkv").touch()

    def test_resolves_relative_path_inside_root(self):
        path = resolve_media_path(self.root, "Show/S01E01.mkv")
        self.assertEqual(path, self.root / "Show" / "S01E01.mkv")

    def test_resolves_absolute_path_inside_root(self):
        path = resolve_media_path(self.root, self.root / "Show")
        self.assertEqual(path, self.root / "Show")

    def test_empty_path_resolves_to_root_itself(self):
        self.assertEqual(resolve_media_path(self.root, ""), self.root)

    def test_relative_traversal_rejected(self):
        with self.assertRaises(OutputSafetyError):
            resolve_media_path(self.root, "../../etc/passwd")

    def test_absolute_path_outside_root_rejected(self):
        with self.assertRaises(OutputSafetyError):
            resolve_media_path(self.root, "/etc/passwd")

    def test_must_exist_rejects_missing_path(self):
        with self.assertRaises(OutputSafetyError):
            resolve_media_path(self.root, "Show/S99E99.mkv", must_exist=True)

    def test_must_exist_accepts_present_path(self):
        path = resolve_media_path(self.root, "Show/S01E01.mkv", must_exist=True)
        self.assertTrue(path.is_file())


class WriteSrtAtomicTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_writes_new_file(self):
        target = self.root / "S01E01.en.srt"
        self.assertTrue(write_srt_atomic(target, "1\n00:00:00,000 --> 00:00:01,000\nHello\n",
                                         allow_overwrite=False))
        self.assertTrue(target.exists())
        self.assertIn("Hello", target.read_text())

    def test_keep_never_overwrites_existing(self):
        target = self.root / "S01E01.en.srt"
        target.write_text("original")
        result = write_srt_atomic(target, "new content", allow_overwrite=False)
        self.assertFalse(result)
        self.assertEqual(target.read_text(), "original")

    def test_replace_overwrites_when_authorized(self):
        target = self.root / "S01E01.en.srt"
        target.write_text("original")
        result = write_srt_atomic(target, "new content", allow_overwrite=True)
        self.assertTrue(result)
        self.assertEqual(target.read_text(), "new content")

    def test_no_temp_file_left_behind_after_success(self):
        target = self.root / "S01E01.en.srt"
        write_srt_atomic(target, "content", allow_overwrite=False)
        leftovers = [p for p in self.root.iterdir() if p != target]
        self.assertEqual(leftovers, [])

    def test_protected_suffix_never_writable_even_with_overwrite_true(self):
        for suffix in PROTECTED_SUFFIXES:
            with self.subTest(suffix=suffix):
                target = self.root / f"S01E01{suffix}"
                with self.assertRaises(OutputSafetyError):
                    write_srt_atomic(target, "malicious content", allow_overwrite=True)
                self.assertFalse(target.exists())

    def test_protected_suffix_rejected_even_though_file_does_not_exist(self):
        target = self.root / "S01E01.en.hi.srt"
        with self.assertRaises(OutputSafetyError):
            write_srt_atomic(target, "x", allow_overwrite=False)


if __name__ == "__main__":
    unittest.main()
