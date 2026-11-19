import os
import shutil
import subprocess
import tempfile
import time
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

    def test_malformed_ssh_options_is_board_error(self):
        runner = mock.Mock()
        settings = FetchSettings(source="ssh", ssh_options='-o "IdentityFile=C:/키 경로')  # 따옴표 짝이 안 맞음
        with self.assertRaises(BoardError) as raised:
            run_action(settings, "status", NOW, runner)
        self.assertIn("ssh 옵션", str(raised.exception))
        runner.assert_not_called()

    def test_unknown_action(self):
        with self.assertRaises(BoardError):
            run_action(SETTINGS, "rm", NOW, mock.Mock())

    def test_timeout_is_board_error(self):
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("ssh", 45))
        with self.assertRaises(BoardError) as raised:
            run_action(SETTINGS, "start", NOW, runner)
        self.assertIn("45초", str(raised.exception))

    def test_stop_timeout_points_to_saved_log_status(self):
        runner = mock.Mock(side_effect=subprocess.TimeoutExpired("ssh", 150))
        with self.assertRaises(BoardError) as raised:
            run_action(SETTINGS, "stop", NOW, runner)
        self.assertIn("마지막 로그 저장", str(raised.exception))

    def test_log_saving_actions_wait_longer_than_board_stop(self):
        # 보드 stop은 종료 대기 10초 + 복사. 복사에 2분 넘게 여유를 둔다.
        for name in ("stop", "restart", "poweroff"):
            self.assertGreaterEqual(ACTIONS[name].timeout, 150, name)

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

    def test_systemd_stop_timeout_covers_log_copy(self):
        # 종료 때 aesactl stop의 로그 복사가 systemd TimeoutStopSec에 끊기지 않게, ssh stop 제한 시간 이상으로 둔다.
        import re

        text = (ROOT / "board" / "install_board.sh").read_text(encoding="utf-8")
        seconds = int(re.search(r"^TimeoutStopSec=(\d+)$", text, re.M).group(1))
        self.assertGreaterEqual(seconds, ACTIONS["stop"].timeout)

    def test_sudoers_account_matches_monitor_default(self):
        text = (ROOT / "board" / "install_board.sh").read_text(encoding="utf-8")
        self.assertIn(f"ACCOUNT=${{AESA_ACCOUNT:-{DEFAULT_SSH_TARGET.split('@')[0]}}}", text)


if __name__ == "__main__":
    unittest.main()


# 가짜 처리부. run_aesa.sh가 /proc/PID/exe로 작업 폴더의 adp인지 확인하므로 셸 스크립트가 아닌 실행 파일이어야 한다.
FAKE_ADP_SOURCE = r"""
#include <signal.h>
#include <stdio.h>
#include <sys/stat.h>
#include <unistd.h>
static volatile sig_atomic_t stop;
static void on_term(int signal_number) { (void)signal_number; stop = 1; }
int main(void) {
    mkdir("logs", 0755);
    FILE *log = fopen("logs/adp_20261119_100000.log", "w");
    if (log) { fputs("run\n", log); fclose(log); }
    signal(SIGTERM, on_term);
    while (!stop) usleep(100000);
    return 0;
}
"""
C_COMPILER = shutil.which("cc") or shutil.which("gcc")


@unittest.skipIf(os.name == "nt" or not shutil.which("sh") or not shutil.which("sha256sum") or not shutil.which("nohup")
                 or not C_COMPILER, "POSIX 셸과 C 컴파일러 필요")
class RunAesaScriptTest(unittest.TestCase):
    """보드 run_aesa.sh를 가짜 id·ip·date·netstat·poweroff와 가짜 adp로 실행한다(보드 없이 실패 처리 확인)."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(self._cleanup)
        self.stub = self.folder / "stub"
        self.fat = self.folder / "fat" / "aesa"
        self.work = self.folder / "work"
        for path in (self.stub, self.fat, self.work):
            path.mkdir(parents=True)
        stubs = {
            "id": "echo 0",
            "ip": f'case "$*" in *"-o addr"*) echo "2: eth0 inet 192.168.10.20/24";; '
                  f'*"addr show"*) echo "    inet 192.168.10.20/24";; esac; exit 0',
            "date": f'[ "$1" = "-s" ] && exit 0; /bin/date "$@"',
            "netstat": f'[ -f "{self.folder}/listen" ] && [ -f "{self.work}/adp.pid" ] && '
                       f'kill -0 "$(cut -d" " -f1 "{self.work}/adp.pid")" 2>/dev/null && '
                       f'echo "tcp 0 0 0.0.0.0:3200 0.0.0.0:* LISTEN"; exit 0',
            "poweroff": f'touch "{self.folder}/powered-off"',
        }
        for name, body in stubs.items():
            path = self.stub / name
            path.write_text(f"#!/bin/sh\n{body}\n", encoding="utf-8")
            path.chmod(0o755)
        source = self.folder / "fake_adp.c"
        source.write_text(FAKE_ADP_SOURCE, encoding="utf-8")
        subprocess.run([C_COMPILER, "-o", str(self.fat / "adp"), str(source)], check=True)
        (self.fat / "aesa.env").write_text("COMM_SERVER_ADDRESS=192.168.10.20\nCOMM_SERVER_PORT=3200\n",
                                           encoding="utf-8")
        subprocess.run("sha256sum adp > SHA256SUMS", shell=True, cwd=self.fat, check=True)
        text = (ROOT / "board" / "run_aesa.sh").read_text(encoding="utf-8")
        replacements = {
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH": f"PATH={self.stub}:$PATH",
            "WORK_DIR=/opt/aesa": f"WORK_DIR={self.work}",
            "SD_DEVICE=/dev/mmcblk0p1": "SD_DEVICE=/dev/aesa-test-none",
            "PORT_WAIT_SECONDS=15": "PORT_WAIT_SECONDS=2",
            "STOP_WAIT_SECONDS=10": "STOP_WAIT_SECONDS=2",
            "LOCK_LINK=/run/aesactl.lock-owner": f"LOCK_LINK={self.folder}/aesactl.lock-owner",
        }
        for old, new in replacements.items():
            self.assertIn(old, text)
            text = text.replace(old, new)
        self.script = self.fat / "run_aesa.sh"
        self.script.write_text(text, encoding="utf-8")

    def _cleanup(self):
        pid_file = self.work / "adp.pid"
        if pid_file.is_file():
            subprocess.run(["kill", "-9", pid_file.read_text().split()[0]], capture_output=True)
        for process in getattr(self, "others", []):
            process.kill()
            process.wait()
        shutil.rmtree(self.folder, ignore_errors=True)

    def run_script(self, *arguments):
        return subprocess.run(["sh", str(self.script), *arguments], capture_output=True, text=True, timeout=30)

    def start(self):
        (self.folder / "listen").touch()
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result

    def break_fat_logs(self):
        (self.fat / "logs").write_text("FAT에 logs 폴더를 만들 수 없게 같은 이름의 파일", encoding="utf-8")

    def test_start_fails_when_port_never_opens(self):
        result = self.run_script("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("포트가 열리지 않았습니다", result.stderr)

    def test_start_then_stop_copies_logs(self):
        self.start()
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.fat / "logs" / "adp_20261119_100000.log").is_file())

    def test_stop_fails_when_log_folder_cannot_be_made(self):
        self.start()
        self.break_fat_logs()
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("logs 폴더를 만들지 못했습니다", result.stderr)

    def test_stop_fails_when_copy_fails(self):
        self.start()
        (self.fat / "logs").mkdir()
        (self.fat / "logs" / "adp_20261119_100000.log").symlink_to(self.folder / "없는폴더" / "x.log")
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("복사하지 못했습니다", result.stderr)
        self.assertNotIn("로그 복사 완료", result.stdout)
        self.assertIn("실패", self.run_script("status").stdout.split("마지막 로그 저장:")[1].splitlines()[0])

    def test_stop_after_crash_still_saves_logs(self):
        self.start()
        subprocess.run(["kill", "-9", (self.work / "adp.pid").read_text().split()[0]], check=True)
        time.sleep(0.3)
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("이미 종료", result.stdout)
        self.assertFalse((self.work / "adp.pid").exists())
        self.assertTrue((self.fat / "logs" / "adp_20261119_100000.log").is_file())

    def test_poweroff_reports_copy_failure_but_powers_off(self):
        self.start()
        self.break_fat_logs()
        result = self.run_script("poweroff")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("복사하지 못한 채 보드를 끕니다", result.stderr)
        deadline = time.monotonic() + 5
        while not (self.folder / "powered-off").exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertTrue((self.folder / "powered-off").exists())

    def test_restart_reports_copy_failure_but_restarts(self):
        self.start()
        self.break_fat_logs()
        result = self.run_script("restart")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("다시 시작했지만", result.stderr)
        self.assertIn("실행 중", self.run_script("status").stdout)

    def unrelated_process(self):
        """처리부가 죽은 뒤 같은 PID를 받은 다른 프로세스 흉내."""
        process = subprocess.Popen(["sleep", "30"])
        self.others = getattr(self, "others", []) + [process]
        return process

    def test_pid_file_records_start_time_and_boot_id(self):
        self.start()
        pid, started, booted = (self.work / "adp.pid").read_text().split()
        self.assertEqual(self.start_time(int(pid)), started)
        self.assertEqual(Path("/proc/sys/kernel/random/boot_id").read_text().strip(), booted)

    @staticmethod
    def start_time(pid):
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]

    @staticmethod
    def boot_id():
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def test_stop_ignores_other_executable_with_matching_start_time_and_boot(self):
        # 비정상 재부팅 뒤 같은 PID·같은 시작시각을 받은 다른 프로그램 흉내: 실행 파일 확인으로 걸러야 한다.
        for with_boot_id in (False, True):  # 부팅 ID 없는 PID 파일(이전 버전)과 있는 파일
            with self.subTest(with_boot_id=with_boot_id):
                other = self.unrelated_process()
                line = f"{other.pid} {self.start_time(other.pid)}" + (f" {self.boot_id()}" if with_boot_id else "")
                (self.work / "adp.pid").write_text(line + "\n", encoding="utf-8")
                result = self.run_script("stop")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("이미 종료", result.stdout)
                self.assertIsNone(other.poll(), "다른 프로세스에 신호를 보냈음")

    def test_pid_file_from_previous_boot_is_not_running(self):
        self.start()
        pid, started, _ = (self.work / "adp.pid").read_text().split()
        (self.work / "adp.pid").write_text(f"{pid} {started} 00000000-0000-0000-0000-000000000000\n",
                                           encoding="utf-8")
        self.assertIn("실행 중 아님", self.run_script("status").stdout)
        subprocess.run(["kill", "-9", pid], check=True)  # 정리(이 프로세스는 이제 PID 파일로 찾지 못함)

    def test_stop_does_not_signal_reused_pid(self):
        other = self.unrelated_process()
        (self.work / "adp.pid").write_text(f"{other.pid} 1\n", encoding="utf-8")  # 시작시각이 다름
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("이미 종료", result.stdout)
        self.assertIsNone(other.poll(), "다른 프로세스에 신호를 보냈음")
        self.assertFalse((self.work / "adp.pid").exists())

    def test_start_ignores_old_pid_file_of_other_process(self):
        other = self.unrelated_process()
        (self.work / "adp.pid").write_text(f"{other.pid}\n", encoding="utf-8")  # 시작시각 없는 예전 형식
        result = self.start()
        self.assertNotIn("이미 실행 중", result.stdout)
        self.assertIn("실행 중: PID", result.stdout)
        self.assertIsNone(other.poll())

    def test_stop_does_not_signal_reused_pid_in_old_pid_file(self):
        other = self.unrelated_process()
        (self.work / "adp.pid").write_text(f"{other.pid}\n", encoding="utf-8")  # 시작시각 없는 예전 형식
        result = self.run_script("stop")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIsNone(other.poll(), "다른 프로세스에 신호를 보냈음")

    def test_second_start_fails_while_hung_processor_keeps_port_closed(self):
        self.assertNotEqual(self.run_script("start").returncode, 0)  # 포트가 열리지 않아 실패, 처리부는 남음
        result = self.run_script("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("이미 실행 중", result.stdout)
        self.assertIn("포트가 열리지 않았습니다", result.stderr)

    def test_second_start_succeeds_when_running_and_listening(self):
        self.start()
        result = self.run_script("start")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("이미 실행 중", result.stdout)
        self.assertIn("실행 중: PID", result.stdout)

    def test_second_save_skips_logs_already_on_sd(self):
        self.start()
        self.assertEqual(self.run_script("stop").returncode, 0)
        result = self.run_script("savelog")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("0개 복사", result.stdout)
        self.assertIn("완료: 복사 0개", self.run_script("status").stdout)

    def test_log_saving_finishes_after_ssh_output_is_gone(self):
        # 모니터가 제한 시간에 ssh를 끊어 출력 받을 곳이 사라져도(SIGPIPE) 복사를 끝까지 하고 결과를 남긴다.
        self.start()
        command = f'sh "{self.script}" stop | head -c 0'
        subprocess.run(command, shell=True, timeout=30)
        self.assertTrue((self.fat / "logs" / "adp_20261119_100000.log").is_file())
        self.assertIn("완료: 복사", (self.work / "savelog.state").read_text(encoding="utf-8"))

    def hold_lock(self, pid, command="stop"):
        lock = self.folder / "aesactl.lock-owner"
        os.symlink(f"{pid} {command}", lock)
        return lock

    def test_command_is_refused_while_another_holds_the_lock(self):
        # 앞 명령(예: 중지 중 로그 복사)이 끝나기 전에 다른 창·노트북에서 보낸 시작은 거부한다.
        other = self.unrelated_process()
        lock = self.hold_lock(other.pid)
        (self.folder / "listen").touch()
        result = self.run_script("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("다른 aesactl 명령이 실행 중", result.stderr)
        self.assertFalse((self.work / "adp.pid").exists(), "처리부를 띄웠음")
        self.assertTrue(lock.is_symlink(), "남의 잠금을 지웠음")

    def test_status_is_not_blocked_by_the_lock(self):
        other = self.unrelated_process()
        self.hold_lock(other.pid)
        result = self.run_script("status")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stale_lock_is_taken_over_and_released(self):
        dead = subprocess.Popen(["true"])
        dead.wait()
        lock = self.hold_lock(dead.pid)
        self.start()
        self.assertFalse(lock.is_symlink(), "명령이 끝났는데 잠금이 남음")


    def test_simultaneous_commands_run_one_at_a_time(self):
        # 두 노트북에서 동시에 시작을 누름: 하나만 실행되고 다른 하나는 잠금 때문에 거부된다.
        (self.folder / "listen").touch()
        processes = [subprocess.Popen(["sh", str(self.script), "start"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(2)]
        results = [(process.wait(timeout=30), process.stderr.read()) for process in processes]
        codes = sorted(code for code, _ in results)
        self.assertEqual(codes[0], 0, results)
        self.assertNotEqual(codes[1], 0, results)
        self.assertTrue(any("다른 aesactl 명령이 실행 중" in error for _, error in results), results)
        for process in processes:
            process.stdout.close()
            process.stderr.close()
