import json
import unittest

import job_config


class CaptureTests(unittest.TestCase):
    def test_is_json_serialisable_and_versioned(self):
        snap = job_config.capture({})
        json.dumps(snap)
        self.assertEqual(snap["version"], job_config.SNAPSHOT_VERSION)
        self.assertEqual(snap["asr_defaults"]["model_name"], "large-v3")
        self.assertIsInstance(snap["asr_defaults"]["temperature"], list)
        self.assertIn("max_cps", snap["subtitle_constraints"])

    def test_records_behaviour_flags_and_defaults_missing_ones_to_none(self):
        snap = job_config.capture({"SUBTITLE_AI_NAME_CORRECTION": "off"})
        self.assertEqual(snap["env"]["SUBTITLE_AI_NAME_CORRECTION"], "off")
        self.assertIsNone(snap["env"]["SUBTITLE_AI_ASR_HOTWORDS"])

    def test_never_captures_credentials_hosts_or_paths(self):
        env = {"SUBTITLE_AI_API_KEY": "s3cret", "TMDB_API_KEY": "k", "TVDB_API_PIN": "p",
               "PLEX_TOKEN": "t", "FAILURE_WEBHOOK_URL": "http://hooks.internal/x",
               "TRANSLATE_SERVER_URL": "http://gpu-box:8091", "SUBTITLE_AI_MEDIA_ROOT": "/data"}
        text = json.dumps(job_config.capture(env))
        for secret in ("s3cret", "hooks.internal", "gpu-box", "TMDB_API_KEY", "/data"):
            self.assertNotIn(secret, text)
        self.assertTrue(job_config.capture(env)["remote_translation"])

    def test_every_allowlisted_variable_is_a_real_setting(self):
        for name in job_config.BEHAVIOUR_ENV:
            self.assertTrue(name.startswith("SUBTITLE_AI_"), name)
