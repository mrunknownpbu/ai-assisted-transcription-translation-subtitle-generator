import io
import json
import logging
import unittest

import logging_setup


class LoggingSetupTests(unittest.TestCase):
    def setUp(self):
        self.root = logging.getLogger()
        self._saved = (self.root.handlers[:], self.root.level)
        self.addCleanup(self._restore)
        self.stream = io.StringIO()

    def _restore(self):
        self.root.handlers[:], level = self._saved
        self.root.setLevel(level)

    def _emit(self, env, job_id=None):
        logging_setup.configure(env, self.stream)
        log = logging.getLogger("t")
        if job_id:
            with logging_setup.job_context(job_id):
                log.info("hello %s", "world")
        else:
            log.info("hello %s", "world")
        return self.stream.getvalue().strip()

    def test_text_format_appends_job_id_only_inside_context(self):
        inside = self._emit({}, "abc123")
        self.assertTrue(inside.endswith("t: hello world [job=abc123]"), inside)
        self.stream.seek(0), self.stream.truncate()
        outside = self._emit({})
        self.assertTrue(outside.endswith("t: hello world"), outside)

    def test_json_format(self):
        record = json.loads(self._emit({"SUBTITLE_AI_LOG_FORMAT": "JSON"}, "abc123"))
        self.assertEqual((record["level"], record["logger"], record["message"], record["job_id"]),
                         ("INFO", "t", "hello world", "abc123"))
        self.assertIn("ts", record)

    def test_json_omits_job_id_outside_context_and_includes_exception(self):
        logging_setup.configure({"SUBTITLE_AI_LOG_FORMAT": "json"}, self.stream)
        try:
            raise ValueError("boom")
        except ValueError:
            logging.getLogger("t").exception("failed")
        record = json.loads(self.stream.getvalue())
        self.assertNotIn("job_id", record)
        self.assertIn("ValueError: boom", record["exc"])

    def test_context_is_restored_after_exit(self):
        with logging_setup.job_context("a"):
            with logging_setup.job_context("b"):
                pass
            self.assertEqual(logging_setup._job_id.get(), "a")
        self.assertIsNone(logging_setup._job_id.get())

    def test_log_level_env_and_invalid_fallback(self):
        logging_setup.configure({"SUBTITLE_AI_LOG_LEVEL": "warning"}, self.stream)
        self.assertEqual(self.root.level, logging.WARNING)
        logging_setup.configure({"SUBTITLE_AI_LOG_LEVEL": "loud"}, self.stream)
        self.assertEqual(self.root.level, logging.INFO)


if __name__ == "__main__":
    unittest.main()
