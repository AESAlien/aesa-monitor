import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aesa_monitor.core.settings import (
    FetchSettings, MonitorSettings, load_settings, save_settings, with_fetch,
)
from aesa_monitor.core.config import ConfigError
from aesa_monitor.io import log_fetch
from aesa_monitor.io.log_fetch import FetchError, fetch_logs, scp_command


class LocalFetchTest(unittest.TestCase):
    def test_copies_only_processor_logs_and_skips_same_size(self):
        with tempfile.TemporaryDirectory() as source, tempfile.TemporaryDirectory() as target:
            source_path = Path(source)
            (source_path / "adp_20261112_101500_000.log").write_text("log")
            (source_path / "adp_detection_20261112_101500_001.csv").write_text("csv")
            (source_path / "other.txt").write_text("x")
            settings = FetchSettings(source="local", local_dir=source)
            messages = []
            result = fetch_logs(settings, Path(target) / "logs", messages.append)
            self.assertEqual(sorted(path.name for path in result.copied),
                             ["adp_20261112_101500_000.log", "adp_detection_20261112_101500_001.csv"])
            self.assertEqual(len(messages), 2)
            again = fetch_logs(settings, Path(target) / "logs")
            self.assertEqual(again.copied, [])
            self.assertEqual(len(again.unchanged), 2)
            (source_path / "adp_20261112_101500_000.log").write_text("log grew")
            third = fetch_logs(settings, Path(target) / "logs")
            self.assertEqual([path.name for path in third.copied], ["adp_20261112_101500_000.log"])

    def test_reports_missing_folder_and_empty_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FetchError):
                fetch_logs(FetchSettings(source="local", local_dir=""), Path(directory))
            with self.assertRaises(FetchError):
                fetch_logs(FetchSettings(source="local", local_dir=directory + "/none"), Path(directory))
            with self.assertRaises(FetchError):
                fetch_logs(FetchSettings(source="local", local_dir=directory), Path(directory) / "out")


class SshFetchTest(unittest.TestCase):
    def test_scp_command_uses_legacy_protocol_batch_mode_and_options(self):
        settings = FetchSettings(source="ssh", ssh_target="root@192.168.10.20", ssh_port=2222,
                                 remote_dir="/tmp/aesa/logs/", ssh_options="-o A=1 -o B=two")
        command = scp_command(settings, Path("/data/out"))
        self.assertEqual(command[:2], ["scp", "-O"])
        self.assertIn("BatchMode=yes", command)
        self.assertEqual(command[command.index("-P") + 1], "2222")
        self.assertEqual(command[-2:], ["root@192.168.10.20:/tmp/aesa/logs/adp_*", "/data/out/"])
        self.assertIn("B=two", command)
        self.assertNotIn("-O", scp_command(settings, Path("/x"), legacy_protocol=False))

    def test_retries_without_legacy_flag_and_explains_errors(self):
        settings = FetchSettings(source="ssh")
        old_scp = mock.Mock(returncode=1, stderr="scp: unknown option -- O\nusage: scp", stdout="")
        denied = mock.Mock(returncode=1, stderr="root@192.168.10.20: Permission denied (publickey).", stdout="")
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(log_fetch.shutil, "which", return_value="/usr/bin/scp"), \
                mock.patch.object(log_fetch, "_run", side_effect=[old_scp, denied]) as run:
            with self.assertRaises(FetchError) as context:
                fetch_logs(settings, Path(directory))
        self.assertEqual(run.call_count, 2)
        self.assertNotIn("-O", run.call_args_list[1].args[0])
        self.assertIn("공개키", str(context.exception))


class SettingsTest(unittest.TestCase):
    def test_defaults_when_file_is_missing(self):
        settings = load_settings(Path("/nonexistent/monitor.env"))
        self.assertEqual(settings, MonitorSettings())

    def test_reads_data_source_and_rejects_unknown_value(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.env"
            path.write_text('MONITOR_SOURCE=capture\nMONITOR_CAPTURE_COMMAND="ssh b tcpdump -w - port {port}"\n',
                            encoding="utf-8")
            settings = load_settings(path)
            self.assertEqual(settings.data_source, "capture")
            self.assertEqual(settings.capture_command, "ssh b tcpdump -w - port {port}")
            path.write_text("MONITOR_SOURCE=both\n", encoding="utf-8")
            with self.assertRaises(ConfigError):
                load_settings(path)

    def test_save_keeps_comments_and_round_trips(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "monitor.env"
            path.write_text("# 내 설정\nMONITOR_FONT_SIZE=14\nOTHER_KEY=1\n", encoding="utf-8")
            settings = with_fetch(load_settings(path), source="ssh", ssh_target="root@10.0.0.2",
                                  local_dir="/home/me/aesa logs")
            save_settings(settings, path)
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("# 내 설정\nMONITOR_FONT_SIZE=14\nOTHER_KEY=1\n"))
            loaded = load_settings(path)
            self.assertEqual(loaded, settings)
            self.assertEqual(loaded.font_size, 14)
            self.assertEqual(loaded.fetch.local_dir, "/home/me/aesa logs")


if __name__ == "__main__":
    unittest.main()
