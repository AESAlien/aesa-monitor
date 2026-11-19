"""처리부 보드 제어. 모니터의 [처리부 제어] 창이 이 코드를 실행한다.

보드의 aesactl(보드 aesa/install_board.sh가 /usr/local/sbin에 설치)을 ssh로 부른다.
    ssh petalinux@192.168.10.20 sudo -n /usr/local/sbin/aesactl start '2026-11-19 09:30:00'
ssh 접속 정보(사용자@주소, 포트, 옵션)는 로그 가져오기 설정(monitor.env의 MONITOR_LOG_SSH_*)을 같이 쓴다.
ssh는 비밀번호를 물을 수 없으므로(BatchMode) 보드에 공개키가, sudo에는 비밀번호 없는 aesactl 규칙이 있어야 한다.

명령줄에서도 따로 실행할 수 있다.
    python3 -m aesa_monitor.io.board_control status
    python3 -m aesa_monitor.io.board_control start
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from aesa_monitor.core.settings import DEFAULT_SETTINGS_PATH, FetchSettings, load_settings

AESACTL = "/usr/local/sbin/aesactl"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


@dataclass(frozen=True)
class Action:
    name: str          # aesactl 명령
    label: str         # 버튼 글자
    timeout: float     # ssh 전체 제한 시간(초)
    sends_time: bool   # 노트북 시각을 함께 보내 보드 시각을 맞춘다


# start/restart는 보드에서 최대 10초 포트를 기다리고, stop은 최대 10초 종료를 기다린 뒤 로그를 SD로 복사한다.
ACTIONS: Dict[str, Action] = {action.name: action for action in (
    Action("status", "상태", 20, False),
    Action("start", "시작", 45, True),
    Action("restart", "재시작", 60, True),
    Action("stop", "중지", 45, False),
    Action("settime", "시각 맞추기", 20, True),
    Action("poweroff", "보드 끄기", 45, False),
)}


class BoardError(RuntimeError):
    """보드 명령을 실행하지 못했다. 메시지는 사용자에게 그대로 보여준다."""


@dataclass
class BoardResult:
    action: Action
    output: str


def remote_command(action: Action, now: Optional[datetime] = None) -> str:
    """보드 셸에서 실행할 명령 문자열. 시각은 한 인자로 따옴표에 넣는다."""
    parts = ["sudo", "-n", AESACTL, action.name]
    if action.sends_time:
        parts.append((now or datetime.now()).strftime(TIME_FORMAT))
    return " ".join(shlex.quote(part) for part in parts)


def ssh_command(settings: FetchSettings, action: Action, now: Optional[datetime] = None) -> List[str]:
    command = ["ssh", "-p", str(settings.ssh_port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    command += shlex.split(settings.ssh_options)
    command += [settings.ssh_target, remote_command(action, now)]
    return command


def run_action(settings: FetchSettings, name: str, now: Optional[datetime] = None,
               runner: Optional[Callable[..., subprocess.CompletedProcess]] = None) -> BoardResult:
    action = ACTIONS.get(name)
    if action is None:
        raise BoardError(f"알 수 없는 명령: {name}")
    if runner is None:
        if shutil.which("ssh") is None:
            raise BoardError("ssh 명령이 없습니다 (Windows: 선택적 기능 'OpenSSH 클라이언트', 리눅스: openssh-client)")
        runner = subprocess.run
    command = ssh_command(settings, action, now)
    try:
        completed = runner(command, capture_output=True, text=True, timeout=action.timeout,
                           stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired as error:
        raise BoardError(f"보드가 {action.timeout:.0f}초 안에 답하지 않았습니다 ({action.label}). "
                         "보드에서 aesactl status로 확인하세요.") from error
    except OSError as error:
        raise BoardError(f"ssh를 실행하지 못했습니다: {error}") from error
    output = (completed.stdout or "").strip()
    if completed.returncode != 0:
        raise BoardError(explain_error(completed.stderr or "", output, settings))
    return BoardResult(action, output)


def explain_error(stderr: str, stdout: str, settings: FetchSettings) -> str:
    text = "\n".join(part for part in (stdout.strip(), stderr.strip()) if part)
    lines = text.splitlines()
    last = lines[-1] if lines else "알 수 없는 오류"
    lower = text.lower()
    if "timed out" in lower or "no route" in lower or "unreachable" in lower or "connection refused" in lower:
        hint = (f"보드({settings.ssh_target})에 접속할 수 없습니다. 보드 전원·부팅, 랜선, "
                "노트북 IP(setup_network.bat)를 확인하세요.")
    elif "permission denied" in lower:
        hint = ("보드가 노트북 키를 받지 않았습니다. prepare_board_sd.bat으로 SD에 공개키를 넣고 "
                "보드에서 install_board.sh를 실행했는지 확인하세요.")
    elif "sudo" in lower and ("password is required" in lower or "terminal is required" in lower
                              or "sudoers" in lower or "not allowed" in lower):
        hint = ("보드 sudo 규칙이 없습니다. 보드에서 install_board.sh를 실행했는지, "
                f"계정({settings.ssh_target.split('@')[0]})이 맞는지 확인하세요.")
    elif "aesactl" in lower and ("not found" in lower or "no such file" in lower):
        hint = f"보드에 {AESACTL}이 없습니다. 보드에서 install_board.sh를 한 번 실행하세요."
    elif "[aesa]" in text:
        hint = "보드에서 명령이 실패했습니다."
    else:
        hint = "ssh 출력을 확인하세요."
    detail = text if "[aesa]" in text else last
    return f"{hint}\n{detail}"


def main(arguments: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m aesa_monitor.io.board_control",
                                     description="처리부 보드의 aesactl을 ssh로 실행한다")
    parser.add_argument("action", choices=sorted(ACTIONS))
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH,
                        help="모니터 설정 파일 (기본값: 프로젝트 폴더의 monitor.env)")
    options = parser.parse_args(arguments)
    settings = load_settings(options.settings).fetch
    try:
        result = run_action(settings, options.action)
    except BoardError as error:
        print(f"실패: {error}", file=sys.stderr)
        return 1
    print(result.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
