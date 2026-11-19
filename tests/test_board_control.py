import os
import shutil
import subprocess
import sys
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
    """보드 run_aesa.sh를 가짜 id·ip·date·netstat·poweroff와 가짜 adp로 실행한다(보드 없이 실패 처리 확인).

    잠금은 flock이 있으면 flock(이 클래스), 없으면 심볼릭 링크(RunAesaScriptLinkLockTest)로 같은 시험을 돈다.
    """

    use_flock = True

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
            "date": f'[ "$1" = "-s" ] && {{ echo "$2" >> "{self.folder}/time-set"; exit 0; }}; /bin/date "$@"',
            "netstat": f'[ -f "{self.folder}/listen" ] && [ -f "{self.work}/adp.pid" ] && '
                       f'kill -0 "$(cut -d" " -f1 "{self.work}/adp.pid")" 2>/dev/null && '
                       f'echo "tcp 0 0 0.0.0.0:3200 0.0.0.0:* LISTEN"; exit 0',
            "poweroff": f'touch "{self.folder}/powered-off"',
            # SD가 가득 찬 것처럼: fail-copy 파일이 있으면 로그 임시 사본(.part) 복사만 실패한다
            "cp": f'for last; do :; done; case "$last" in *.part) [ -f "{self.folder}/fail-copy" ] && exit 1;; esac; '
                  f'exec /bin/cp "$@"',
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
            "LOCK_FILE=/run/aesactl.lock": f"LOCK_FILE={self.folder}/aesactl.lock",
            "LOCK_LINK=/run/aesactl.lock-owner": f"LOCK_LINK={self.folder}/aesactl.lock-owner",
            # 시험용: 잠금을 잡은 뒤 AESA_TEST_HOLD초 머문다(겹침 시험). 실제 스크립트에는 없다.
            'start|stop|restart|poweroff|savelog|boot|settime) acquire_lock "$1" ;;':
                'start|stop|restart|poweroff|savelog|boot|settime) acquire_lock "$1"; '
                '[ -n "$AESA_TEST_TRACE" ] && echo "$$ $(date +%s.%N) in" >> "$AESA_TEST_TRACE"; '
                'sleep "${AESA_TEST_HOLD:-0}"; '
                '[ -n "$AESA_TEST_TRACE" ] && echo "$$ $(date +%s.%N) out" >> "$AESA_TEST_TRACE" ;;',
        }
        if not self.use_flock:
            replacements["FLOCK=$(command -v flock 2>/dev/null)"] = "FLOCK="
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

    def run_script(self, *arguments, env=None):
        return subprocess.run(["sh", str(self.script), *arguments], capture_output=True, text=True, timeout=30,
                              env=None if env is None else {**os.environ, **env})

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
        (self.folder / "fail-copy").touch()
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("복사하지 못했습니다", result.stderr)
        self.assertNotIn("로그 복사 완료", result.stdout)
        self.assertIn("실패", self.run_script("status").stdout.split("마지막 로그 저장:")[1].splitlines()[0])
        self.assertEqual([path.name for path in (self.fat / "logs").iterdir() if path.name.endswith(".part")], [],
                         "임시 파일이 남음")

    def test_directory_at_log_name_is_a_copy_failure(self):
        # mv는 대상이 폴더면 그 안으로 옮기고 성공해 버린다. 복사 실패로 알려야 한다.
        self.start()
        (self.fat / "logs" / "adp_20261119_100000.log").mkdir(parents=True)
        result = self.run_script("stop")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.fat / "logs" / "adp_20261119_100000.log").iterdir()), [])

    def test_same_size_but_different_log_is_copied_again(self):
        # 보드 시계가 되돌아가 같은 이름의 로그가 다시 생기거나, 이전 복사가 같은 크기로 깨졌을 때
        self.start()
        self.assertEqual(self.run_script("stop").returncode, 0)
        saved = self.fat / "logs" / "adp_20261119_100000.log"
        board = self.work / "logs" / "adp_20261119_100000.log"
        saved.write_bytes(b"X" * board.stat().st_size)  # 크기는 같고 내용이 다름
        result = self.run_script("savelog")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(saved.read_bytes(), board.read_bytes())

    def test_start_while_running_does_not_change_board_time(self):
        (self.folder / "listen").touch()
        self.assertEqual(self.run_script("start", "2026-11-19 10:00:00").returncode, 0)
        result = self.run_script("start", "2026-11-19 11:00:00")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("시각은 바꾸지 않음", result.stdout)
        self.assertEqual((self.folder / "time-set").read_text(encoding="utf-8").split("\n")[:-1],
                         ["2026-11-19 10:00:00"])

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

    # 잠금 시험. hold_lock은 다른 명령이 잠금을 잡은 상태를 흉내 낸다.

    @staticmethod
    def start_time(pid):
        return Path(f"/proc/{pid}/stat").read_text().rsplit(") ", 1)[1].split()[19]

    @staticmethod
    def boot_id():
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()

    def hold_lock(self, command="stop"):
        """살아 있는 다른 명령이 잠금을 잡은 상태. 잡은 프로세스를 돌려준다(끝내면 풀림)."""
        if self.use_flock:
            holder = subprocess.Popen(
                [sys.executable, "-c",
                 "import fcntl, sys, time; f = open(sys.argv[1], 'a'); fcntl.flock(f, fcntl.LOCK_EX); "
                 "print('locked', flush=True); time.sleep(60)", str(self.folder / "aesactl.lock")],
                stdout=subprocess.PIPE, text=True)
            self.assertEqual(holder.stdout.readline().strip(), "locked")
            (self.folder / "aesactl.lock.info").write_text(f"{command}, PID {holder.pid}\n", encoding="utf-8")
        else:
            holder = subprocess.Popen(["sleep", "60"])
            os.symlink(f"{holder.pid} {self.start_time(holder.pid)} {self.boot_id()} {command}",
                       self.folder / "aesactl.lock-owner")
        self.others = getattr(self, "others", []) + [holder]
        return holder

    def lock_free(self):
        return not (self.folder / "aesactl.lock-owner").is_symlink()

    def test_command_is_refused_while_another_holds_the_lock(self):
        # 앞 명령(예: 중지 중 로그 복사)이 끝나기 전에 다른 창·노트북에서 보낸 시작은 거부한다.
        self.hold_lock()
        (self.folder / "listen").touch()
        result = self.run_script("start")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("다른 aesactl 명령이 실행 중", result.stderr)
        self.assertIn("stop", result.stderr)  # 누가 잡았는지 안내
        self.assertFalse((self.work / "adp.pid").exists(), "처리부를 띄웠음")
        if not self.use_flock:
            self.assertTrue((self.folder / "aesactl.lock-owner").is_symlink(), "남의 잠금을 지웠음")

    def test_status_is_not_blocked_by_the_lock(self):
        self.hold_lock()
        self.assertEqual(self.run_script("status").returncode, 0)

    def test_lock_of_killed_command_is_taken_over_and_released(self):
        holder = self.hold_lock()
        holder.kill()
        holder.wait()
        self.start()
        self.assertTrue(self.lock_free(), "명령이 끝났는데 잠금이 남음")

    def test_waits_for_lock_when_asked(self):
        # init(종료·부팅)은 AESA_LOCK_WAIT로 기다린다. 앞 명령이 2초 뒤 끝나면 이어서 실행된다.
        holder = self.hold_lock()
        killer = subprocess.Popen(["sh", "-c", f"sleep 2; kill {holder.pid}"])
        self.others = self.others + [killer]
        result = self.run_script("savelog", env={"AESA_LOCK_WAIT": "8"})
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_running_processor_does_not_keep_the_lock(self):
        # 처리부(adp)가 잠금을 물려받으면 도는 동안 모든 명령이 거부된다.
        self.start()
        result = self.run_script("savelog")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_settime_takes_the_lock(self):
        self.hold_lock()
        self.assertNotEqual(self.run_script("settime", "2026-11-19 10:00:00").returncode, 0)

    def run_together(self, count=4, hold="1"):
        """명령 count개를 동시에 실행한다. 잠금 안에 머문 구간(들어감·나옴 시각)과 종료 코드를 돌려준다."""
        trace = self.folder / "trace"
        trace.write_text("", encoding="utf-8")
        env = {**os.environ, "AESA_TEST_HOLD": hold, "AESA_TEST_TRACE": str(trace)}
        processes = [subprocess.Popen(["sh", str(self.script), "savelog"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True, env=env) for _ in range(count)]
        codes = []
        for process in processes:
            codes.append(process.wait(timeout=60))
            process.stdout.close()
            process.stderr.close()
        spans = {}
        for line in trace.read_text(encoding="utf-8").splitlines():
            pid, at, side = line.split()
            spans.setdefault(pid, {})[side] = float(at)
        return codes, sorted((span["in"], span["out"]) for span in spans.values())

    def assert_no_overlap(self, spans):
        for (_, first_out), (second_in, _) in zip(spans, spans[1:]):
            self.assertLessEqual(first_out, second_in, f"잠금 안 구간이 겹침: {spans}")

    def test_simultaneous_commands_never_overlap(self):
        # 두 노트북에서 동시에 누름: 잠금 안에는 한 번에 하나만 들어간다. 바로 거부되는 것도 있어야 한다.
        for _ in range(3):
            codes, spans = self.run_together()
            self.assert_no_overlap(spans)
            self.assertGreaterEqual(codes.count(0), 1)
            self.assertGreaterEqual(len([code for code in codes if code != 0]), 1, codes)
            self.assertTrue(self.lock_free())


class RunAesaScriptLinkLockTest(RunAesaScriptTest):
    """flock이 없는 보드: 심볼릭 링크 잠금으로 같은 시험을 돈다. 링크 잠금만의 경우를 더한다."""

    use_flock = False

    def test_reused_pid_is_not_taken_as_lock_owner(self):
        # 앞 명령이 강제 종료되고 그 PID를 다른 프로세스가 받음: 시작시각이 달라 주인이 아니다.
        other = subprocess.Popen(["sleep", "60"])
        self.others = getattr(self, "others", []) + [other]
        os.symlink(f"{other.pid} 1 {self.boot_id()} stop", self.folder / "aesactl.lock-owner")
        self.start()
        self.assertIsNone(other.poll())

    def abandoned(self):
        """강제 종료된 명령이 남긴 잠금 주인 신원(없는 PID)."""
        return f"999999 1 {self.boot_id()}"

    def guard(self, identity, generation):
        return self.folder / f"aesactl.lock-owner.reclaim.{identity.replace(' ', '_')}.{generation}"

    def guards_left(self):
        return sorted(path.name for path in self.folder.glob("aesactl.lock-owner.reclaim*"))

    def test_simultaneous_reclaim_of_abandoned_lock_never_overlaps(self):
        # 버려진 잠금을 여러 명령이 동시에 정리해도 잠금 안에는 한 번에 하나만 들어간다.
        for _ in range(3):
            os.symlink(f"{self.abandoned()} stop", self.folder / "aesactl.lock-owner")
            codes, spans = self.run_together()
            self.assert_no_overlap(spans)
            self.assertGreaterEqual(codes.count(0), 1)
            self.assertTrue(self.lock_free())
            self.assertEqual(self.guards_left(), [], "정리 잠금이 남음")

    def test_reclaim_survives_a_reclaimer_killed_midway(self):
        # 정리하던 명령이 정리 잠금을 잡은 채 강제 종료됨: 그 정리 잠금은 지우지 않고 다음 세대로 정리한다.
        for _ in range(3):
            os.symlink(f"{self.abandoned()} stop", self.folder / "aesactl.lock-owner")
            os.symlink(f"999998 1 {self.boot_id()}", self.guard(self.abandoned(), 1))
            codes, spans = self.run_together()
            self.assert_no_overlap(spans)
            self.assertGreaterEqual(codes.count(0), 1)
            self.assertTrue(self.lock_free())
            self.assertEqual(self.guards_left(), [], "정리 잠금이 남음")

    def test_live_reclaimer_guard_is_never_removed(self):
        # 리뷰의 경우: 1세대 정리 잠금의 주인은 죽었고, 다른 명령이 2세대 정리 잠금을 잡고 정리하는 중.
        # 뒤에 온 명령은 그 정리 잠금을 지우거나 같이 정리하지 않고 기다린다(여기서는 바로 거부).
        os.symlink(f"{self.abandoned()} stop", self.folder / "aesactl.lock-owner")
        os.symlink(f"999998 1 {self.boot_id()}", self.guard(self.abandoned(), 1))
        reclaimer = subprocess.Popen(["sleep", "60"])
        self.others = getattr(self, "others", []) + [reclaimer]
        live = f"{reclaimer.pid} {self.start_time(reclaimer.pid)} {self.boot_id()}"
        os.symlink(live, self.guard(self.abandoned(), 2))
        result = self.run_script("savelog")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("가져가는 중", result.stderr)
        self.assertEqual(os.readlink(self.guard(self.abandoned(), 2)), live)
        self.assertEqual(os.readlink(self.folder / "aesactl.lock-owner"), f"{self.abandoned()} stop")
