import shutil
import subprocess
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

from aesa_monitor.core.settings import (DEFAULT_REMOTE_DIR, DEFAULT_SSH_OPTIONS, DEFAULT_SSH_TARGET, FetchSettings,
                                        LEGACY_SSH_OPTIONS, load_settings, save_settings)
from aesa_monitor.io.board_control import ACTIONS, AESACTL, BoardError, explain_error, remote_command, run_action, \
    ssh_command

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 19, 9, 30, 5)
SETTINGS = FetchSettings(source="ssh", ssh_target="petalinux@192.168.10.20", ssh_port=2222,
                         ssh_options="-o A=1 -o IdentityFile=~/.ssh/aesa_board_ed25519")


def completed(returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


class CommandTest(unittest.TestCase):
    def test_start_sends_laptop_time_as_one_argument(self):
        self.assertEqual(remote_command(ACTIONS["start"], NOW), f"sudo -n {AESACTL} start '2026-11-19 09:30:05'")

    def test_status_has_no_time(self):
        self.assertEqual(remote_command(ACTIONS["status"], NOW), f"sudo -n {AESACTL} status")

    def test_ssh_command_uses_fetch_settings(self):
        command = ssh_command(SETTINGS, ACTIONS["stop"], NOW)
        self.assertEqual(command[:7], ["ssh", "-p", "2222", "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"])
        self.assertEqual(command[7:11], ["-o", "A=1", "-o", "IdentityFile=~/.ssh/aesa_board_ed25519"])
        self.assertEqual(command[-2:], ["petalinux@192.168.10.20", f"sudo -n {AESACTL} stop"])


class RunActionTest(unittest.TestCase):
    def test_success_returns_output(self):
        runner = mock.Mock(return_value=completed(stdout="[aesa] 처리부: 실행 중 (PID 12)\n"))
        result = run_action(SETTINGS, "status", NOW, runner)
        self.assertEqual(result.output, "[aesa] 처리부: 실행 중 (PID 12)")
        self.assertEqual(runner.call_args.kwargs["timeout"], ACTIONS["status"].timeout)

    def test_unknown_action(self):
        with self.assertRaises(BoardError):
            run_action(SETTINGS, "rm", NOW, mock.Mock())

    def test_timeout_is_board_error(self):
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("ssh", 45))
        with self.assertRaises(BoardError) as raised:
            run_action(SETTINGS, "start", NOW, runner)
        self.assertIn("45초", str(raised.exception))

    def test_failure_keeps_board_message(self):
        runner = mock.Mock(return_value=completed(1, stderr="[aesa] 오류: eth0를 켤 수 없습니다.\n"))
        with self.assertRaises(BoardError) as raised:
            run_action(SETTINGS, "start", NOW, runner)
        self.assertIn("eth0를 켤 수 없습니다", str(raised.exception))


class ExplainErrorTest(unittest.TestCase):
    def check(self, stderr, expected):
        self.assertIn(expected, explain_error(stderr, "", SETTINGS))

    def test_hints(self):
        self.check("petalinux@192.168.10.20: Permission denied (publickey).", "prepare_board_sd.bat")
        self.check("sudo: a password is required", "sudo 규칙")
        self.check("sh: /usr/local/sbin/aesactl: not found", "install_board.sh를 한 번")
        self.check("ssh: connect to host 192.168.10.20 port 22: Connection timed out", "접속할 수 없습니다")

    def test_connection_error_wins_over_missing_identity_warning(self):
        stderr = ("Warning: Identity file C:\\Users\\u/.ssh/aesa_board_ed25519 not accessible: No such file or directory.\n"
                  "ssh: connect to host 192.168.10.20 port 22: Connection refused")
        self.check(stderr, "접속할 수 없습니다")


class LegacySettingsTest(unittest.TestCase):
    def test_old_defaults_read_as_new_defaults(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "monitor.env"
            path.write_text("MONITOR_LOG_SSH_TARGET=root@192.168.10.20\nMONITOR_LOG_REMOTE_DIR=/tmp/aesa/logs\n"
                            f'MONITOR_LOG_SSH_OPTIONS="{LEGACY_SSH_OPTIONS.replace("/dev/null", "NUL")}"\n',
                            encoding="utf-8")
            fetch = load_settings(path).fetch
            self.assertEqual(fetch.ssh_target, DEFAULT_SSH_TARGET)
            self.assertEqual(fetch.remote_dir, DEFAULT_REMOTE_DIR)
            self.assertEqual(fetch.ssh_options, DEFAULT_SSH_OPTIONS.replace("/dev/null", "NUL"))
            save_settings(load_settings(path), path)
            self.assertIn(f"MONITOR_LOG_SSH_TARGET={DEFAULT_SSH_TARGET}", path.read_text(encoding="utf-8"))

    def test_custom_values_are_kept(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "monitor.env"
            path.write_text("MONITOR_LOG_SSH_TARGET=root@10.0.0.2\nMONITOR_LOG_REMOTE_DIR=/data/logs\n",
                            encoding="utf-8")
            fetch = load_settings(path).fetch
            self.assertEqual((fetch.ssh_target, fetch.remote_dir), ("root@10.0.0.2", "/data/logs"))


class BoardScriptTest(unittest.TestCase):
    """보드 셸 스크립트: 문법, 줄바꿈(LF), 저장소 기본값과 맞는지."""

    scripts = ("run_aesa.sh", "install_board.sh")

    def test_scripts_are_lf_and_parse(self):
        shell = shutil.which("sh")
        for name in self.scripts:
            path = ROOT / "board" / name
            self.assertNotIn(b"\r\n", path.read_bytes(), name)
            if shell:
                result = subprocess.run([shell, "-n", str(path)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

    def test_work_dir_matches_monitor_default(self):
        text = (ROOT / "board" / "run_aesa.sh").read_text(encoding="utf-8")
        self.assertIn(f"WORK_DIR={DEFAULT_REMOTE_DIR.rsplit('/logs', 1)[0]}\n", text)
        self.assertIn(f"{AESACTL}", (ROOT / "board" / "install_board.sh").read_text(encoding="utf-8"))

    def test_sudoers_account_matches_monitor_default(self):
        text = (ROOT / "board" / "install_board.sh").read_text(encoding="utf-8")
        self.assertIn(f"ACCOUNT=${{AESA_ACCOUNT:-{DEFAULT_SSH_TARGET.split('@')[0]}}}", text)


if __name__ == "__main__":
    unittest.main()
