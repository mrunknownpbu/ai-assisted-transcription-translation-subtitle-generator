"""media.extract_audio: the WAV only ever appears at its final path when ffmpeg succeeded."""
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import media
from errors import MediaError


def _fake_ffmpeg_ok(command, **kwargs):
    Path(command[-1]).write_bytes(b"RIFF-good-audio")
    return subprocess.CompletedProcess(command, 0, "", "")


class ExtractAudioAtomicityTests(unittest.TestCase):
    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.out = Path(self._dir.name) / "work" / "episode.wav"

    def _leftovers(self):
        return sorted(p.name for p in self.out.parent.iterdir()) if self.out.parent.exists() else []

    def test_success_leaves_only_the_final_wav(self):
        with mock.patch.object(media.subprocess, "run", side_effect=_fake_ffmpeg_ok):
            result = media.extract_audio("/data/show/ep.mkv", 1, self.out)
        self.assertEqual(result, self.out)
        self.assertEqual(self.out.read_bytes(), b"RIFF-good-audio")
        self.assertEqual(self._leftovers(), ["episode.wav"])

    def test_command_is_explicit_about_format_and_stdin(self):
        with mock.patch.object(media.subprocess, "run", side_effect=_fake_ffmpeg_ok) as run:
            media.extract_audio("/data/show/ep.mkv", 2, self.out, sample_rate=16000)
        command = run.call_args.args[0]
        self.assertIn("-nostdin", command)
        self.assertEqual(command[command.index("-f") + 1], "wav")
        self.assertEqual(command[command.index("-map") + 1], "0:2")
        self.assertNotEqual(command[-1], str(self.out), "ffmpeg must not write the final path directly")

    def test_failed_ffmpeg_leaves_no_partial_file(self):
        def partial_then_fail(command, **kwargs):
            Path(command[-1]).write_bytes(b"truncated")
            raise subprocess.CalledProcessError(1, command)

        with mock.patch.object(media.subprocess, "run", side_effect=partial_then_fail):
            with self.assertRaises(MediaError):
                media.extract_audio("/data/show/ep.mkv", 1, self.out)
        self.assertFalse(self.out.exists())
        self.assertEqual(self._leftovers(), [])

    def test_timeout_leaves_no_partial_file(self):
        def partial_then_timeout(command, **kwargs):
            Path(command[-1]).write_bytes(b"truncated")
            raise subprocess.TimeoutExpired(command, 1800)

        with mock.patch.object(media.subprocess, "run", side_effect=partial_then_timeout):
            with self.assertRaises(MediaError):
                media.extract_audio("/data/show/ep.mkv", 1, self.out)
        self.assertEqual(self._leftovers(), [])

    def test_failure_keeps_an_earlier_good_wav(self):
        self.out.parent.mkdir(parents=True)
        self.out.write_bytes(b"RIFF-previous-good")

        def fail(command, **kwargs):
            raise subprocess.CalledProcessError(1, command)

        with mock.patch.object(media.subprocess, "run", side_effect=fail):
            with self.assertRaises(MediaError):
                media.extract_audio("/data/show/ep.mkv", 1, self.out)
        self.assertEqual(self.out.read_bytes(), b"RIFF-previous-good")
        self.assertEqual(self._leftovers(), ["episode.wav"])


if __name__ == "__main__":
    unittest.main()
