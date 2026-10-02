import unittest

from startup_guard import InsecureBindError, bind_host, check_bind_safety, is_loopback

DOCKER_ARGV = ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]


class BindHostTests(unittest.TestCase):
    def test_defaults_to_loopback(self):
        self.assertEqual(bind_host(["uvicorn", "main:app"], {}), "127.0.0.1")

    def test_reads_host_flag_both_forms(self):
        self.assertEqual(bind_host(DOCKER_ARGV, {}), "0.0.0.0")
        self.assertEqual(bind_host(["uvicorn", "--host=::", "main:app"], {}), "::")

    def test_env_overrides_argv(self):
        self.assertEqual(bind_host(DOCKER_ARGV, {"SUBTITLE_AI_BIND_HOST": "127.0.0.1"}), "127.0.0.1")

    def test_trailing_host_flag_without_value_is_ignored(self):
        self.assertEqual(bind_host(["uvicorn", "--host"], {}), "127.0.0.1")


class LoopbackTests(unittest.TestCase):
    def test_loopback_forms(self):
        for host in ("127.0.0.1", "127.1.2.3", "::1", "[::1]", "localhost", "LOCALHOST"):
            with self.subTest(host=host):
                self.assertTrue(is_loopback(host))

    def test_non_loopback_forms(self):
        for host in ("0.0.0.0", "::", "", "192.168.1.5", "example.com", "::ffff:0.0.0.0"):
            with self.subTest(host=host):
                self.assertFalse(is_loopback(host))


class CheckBindSafetyTests(unittest.TestCase):
    def test_non_loopback_without_key_refuses(self):
        with self.assertRaises(InsecureBindError) as ctx:
            check_bind_safety(DOCKER_ARGV, {})
        self.assertIn("SUBTITLE_AI_API_KEY", str(ctx.exception))
        self.assertIn("SUBTITLE_AI_ALLOW_INSECURE", str(ctx.exception))

    def test_empty_key_counts_as_unset(self):
        with self.assertRaises(InsecureBindError):
            check_bind_safety(DOCKER_ARGV, {"SUBTITLE_AI_API_KEY": ""})

    def test_key_allows_non_loopback(self):
        check_bind_safety(DOCKER_ARGV, {"SUBTITLE_AI_API_KEY": "secret"})

    def test_loopback_without_key_is_fine(self):
        check_bind_safety(["uvicorn", "main:app"], {})
        check_bind_safety(["uvicorn", "main:app", "--host", "127.0.0.1"], {})

    def test_explicit_opt_out(self):
        for value in ("1", "true", "YES", "on"):
            with self.subTest(value=value):
                check_bind_safety(DOCKER_ARGV, {"SUBTITLE_AI_ALLOW_INSECURE": value})

    def test_falsey_opt_out_values_do_not_opt_out(self):
        for value in ("", "0", "false", "off"):
            with self.subTest(value=value), self.assertRaises(InsecureBindError):
                check_bind_safety(DOCKER_ARGV, {"SUBTITLE_AI_ALLOW_INSECURE": value})


if __name__ == "__main__":
    unittest.main()
