import subprocess
import sys
import unittest
from pathlib import Path

import errors
import gpu
import media
import output
import pipeline
import srt_translation

SUBTITLE_AI_DIR = Path(__file__).resolve().parents[1] / "subtitle_ai"


class ErrorCodeContractTests(unittest.TestCase):
    """Codes are stored on jobs, shown in the UI and sent in webhooks: they
    may be added to, never renamed."""

    def test_codes_are_stable(self):
        self.assertEqual(
            {cls.__name__: cls.code for cls in (
                errors.MediaError, errors.SubtitleParseError, errors.LowConfidenceLanguageError,
                errors.UnsupportedLanguageError, errors.OutputSafetyError,
                errors.InsufficientVramError, errors.GpuLockTimeout)},
            {"MediaError": "MEDIA_ERROR", "SubtitleParseError": "SRT_VALIDATION_ERROR",
             "LowConfidenceLanguageError": "LOW_CONFIDENCE_LANGUAGE",
             "UnsupportedLanguageError": "UNSUPPORTED_LANGUAGE", "OutputSafetyError": "OUTPUT_ERROR",
             "InsufficientVramError": "GPU_RESOURCE_ERROR", "GpuLockTimeout": "GPU_RESOURCE_ERROR"})
        self.assertEqual(errors.UNEXPECTED_CODE, "PIPELINE_ERROR")

    def test_modules_keep_exporting_their_old_names(self):
        self.assertIs(pipeline.LowConfidenceLanguageError, errors.LowConfidenceLanguageError)
        self.assertIs(pipeline.UnsupportedLanguageError, errors.UnsupportedLanguageError)
        self.assertIs(srt_translation.SrtValidationError, errors.SubtitleParseError)
        self.assertIs(output.OutputSafetyError, errors.OutputSafetyError)
        self.assertIs(media.MediaError, errors.MediaError)
        self.assertIs(gpu.InsufficientVramError, errors.InsufficientVramError)
        self.assertIs(gpu.GpuLockTimeout, errors.GpuLockTimeout)

    def test_builtin_bases_are_preserved_for_existing_except_clauses(self):
        self.assertTrue(issubclass(errors.OutputSafetyError, ValueError))
        self.assertTrue(issubclass(errors.SubtitleParseError, ValueError))
        self.assertTrue(issubclass(errors.MediaError, RuntimeError))
        self.assertTrue(issubclass(errors.InsufficientVramError, RuntimeError))
        self.assertTrue(issubclass(errors.GpuLockTimeout, TimeoutError))


class DescribeTests(unittest.TestCase):
    def test_typed_error_reports_code_stage_and_hint(self):
        info = errors.describe(errors.LowConfidenceLanguageError("detected tr at 31%"))
        self.assertEqual((info.code, info.message, info.stage, info.expected),
                         ("LOW_CONFIDENCE_LANGUAGE", "detected tr at 31%", "language_detection", True))
        self.assertIn("manually", info.remediation)

    def test_unexpected_error_is_a_bounded_pipeline_error(self):
        info = errors.describe(KeyError("x" * 2000))
        self.assertEqual(info.code, "PIPELINE_ERROR")
        self.assertFalse(info.expected)
        self.assertLessEqual(len(info.message), errors.UNEXPECTED_MESSAGE_LIMIT)
        self.assertIsNone(info.stage)


class WorkflowBoundaryTests(unittest.TestCase):
    def test_subtitle_translation_workflow_does_not_load_asr(self):
        """Workflow B must never reach ASR; not even by importing it."""
        code = ("import sys; sys.path.insert(0, %r); import srt_translation; "
                "bad = sorted(m for m in ('asr', 'pipeline', 'faster_whisper') if m in sys.modules); "
                "print(','.join(bad))") % str(SUBTITLE_AI_DIR)
        result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
        self.assertEqual(result.stdout.strip(), "", "Workflow B imported ASR-side modules")


if __name__ == "__main__":
    unittest.main()
