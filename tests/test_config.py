import tempfile
import unittest
from pathlib import Path

from aesa_monitor.core.config import ConfigError, TrackingParameters, load_config, parse_endpoint

DATA = Path(__file__).resolve().parent / "data"


class EnvFileTestCase(unittest.TestCase):
    def write_env(self, text):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / ".env"
        path.write_text(text, encoding="utf-8")
        return path


class ConfigTest(EnvFileTestCase):
    def test_reads_server_and_monitor_endpoints_from_example(self):
        config = load_config(DATA / "processor.env.example")

        self.assertEqual(config.server, ("192.168.10.20", 3200))
        self.assertEqual(config.local, ("192.168.10.30", 3400))
        self.assertEqual(config.tracking, TrackingParameters())
        # 통신 캡처에서 연결의 상대를 정할 때 쓰는 주소
        self.assertEqual(config.console, ("192.168.10.10", 3100))
        self.assertEqual(config.simulator, ("192.168.10.100", 3300))

    def test_handles_comments_and_quotes(self):
        path = self.write_env(
            "# comment\n"
            "COMM_SERVER_ADDRESS='127.0.0.1'\n"
            "COMM_SERVER_PORT=43200 # inline\n"
            'COMM_MONITOR_ADDRESS="127.0.0.4"\n'
            "COMM_MONITOR_PORT=43203\n"
        )

        config = load_config(path)

        self.assertEqual(config.server, ("127.0.0.1", 43200))
        self.assertEqual(config.local, ("127.0.0.4", 43203))

    def test_arguments_override_env_values(self):
        config = load_config(DATA / "processor.env.example", "127.0.0.1:1", "127.0.0.2:2")

        self.assertEqual(config.server, ("127.0.0.1", 1))
        self.assertEqual(config.local, ("127.0.0.2", 2))

    def test_rejects_missing_or_invalid_values(self):
        with self.assertRaises(ConfigError):
            load_config(self.write_env("COMM_SERVER_ADDRESS=127.0.0.1\n"))
        with self.assertRaises(ConfigError):
            load_config(None)
        with self.assertRaises(ConfigError):
            parse_endpoint("127.0.0.1")
        with self.assertRaises(ConfigError):
            parse_endpoint("127.0.0.1:70000")


class TrackingParametersTest(EnvFileTestCase):
    def test_reads_tracking_values_and_keeps_defaults_for_missing_keys(self):
        path = self.write_env(
            "COMM_SERVER_ADDRESS=127.0.0.1\nCOMM_SERVER_PORT=43200\n"
            "COMM_MONITOR_ADDRESS=127.0.0.4\nCOMM_MONITOR_PORT=43203\n"
            "TARGET_TRACKING_INTERVAL_MS=500\nTARGET_POSITION_NDS_THRESHOLD=9.5\n"
        )

        tracking = load_config(path).tracking

        self.assertEqual(tracking.tracking_interval_ms, 500)
        self.assertAlmostEqual(tracking.position_nds_threshold, 9.5)
        self.assertEqual(tracking.max_consecutive_tracking_misses, 3)

    def test_rejects_invalid_tracking_value(self):
        path = self.write_env(
            "COMM_SERVER_ADDRESS=127.0.0.1\nCOMM_SERVER_PORT=43200\n"
            "COMM_MONITOR_ADDRESS=127.0.0.4\nCOMM_MONITOR_PORT=43203\n"
            "TARGET_TRACKING_INTERVAL_MS=fast\n"
        )

        with self.assertRaises(ConfigError):
            load_config(path)


if __name__ == "__main__":
    unittest.main()
