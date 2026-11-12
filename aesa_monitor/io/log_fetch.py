"""처리부 로그 가져오기. 모니터의 [로그 가져오기] 버튼이 이 코드를 실행한다.

명령줄에서도 따로 실행할 수 있다.
    python3 -m aesa_monitor.io.log_fetch --dest ~/aesa-records/test            # monitor.env 설정 사용
    python3 -m aesa_monitor.io.log_fetch --source ssh --target root@192.168.10.20 --dest DIR
    python3 -m aesa_monitor.io.log_fetch --source local --local-dir ~/aesa/logs --dest DIR

가져오는 파일: adp_*.log(메시지 로그), adp_detection_*.csv(탐지 CSV).
local은 파일 크기가 같으면 다시 복사하지 않는다. ssh는 scp로 매번 모두 가져온다.
ssh는 비밀번호를 물을 수 없으므로(BatchMode) 보드에 공개키가 등록되어 있어야 한다.
"""

from __future__ import annotations

import argparse
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional

from aesa_monitor.core.settings import DEFAULT_SETTINGS_PATH, FetchSettings, load_settings

LOG_PATTERNS = ("adp_*.log", "adp_detection_*.csv")
SSH_TIMEOUT_SECONDS = 120
Progress = Callable[[str], None]


class FetchError(RuntimeError):
    """로그를 가져오지 못했다. 메시지는 사용자에게 그대로 보여준다."""


@dataclass
class FetchResult:
    destination: Path
    copied: List[Path] = field(default_factory=list)
    unchanged: List[Path] = field(default_factory=list)

    @property
    def files(self) -> List[Path]:
        return sorted(self.copied + self.unchanged)

    def describe(self) -> str:
        return (f"{len(self.copied)}개 가져옴, {len(self.unchanged)}개는 이미 같음 → {self.destination}")


def fetch_logs(settings: FetchSettings, destination: Path, progress: Optional[Progress] = None) -> FetchResult:
    report = progress or (lambda _text: None)
    destination = Path(destination).expanduser()
    destination.mkdir(parents=True, exist_ok=True)
    if settings.source == "local":
        return _fetch_local(settings, destination, report)
    if settings.source == "ssh":
        return _fetch_ssh(settings, destination, report)
    raise FetchError(f"알 수 없는 가져오기 방식: {settings.source}")


def _log_files(directory: Path) -> List[Path]:
    files: List[Path] = []
    for pattern in LOG_PATTERNS:
        files.extend(directory.glob(pattern))
    return sorted(set(files))


def _fetch_local(settings: FetchSettings, destination: Path, report: Progress) -> FetchResult:
    if not settings.local_dir:
        raise FetchError("처리부 로그 폴더가 설정되지 않았습니다 (MONITOR_LOG_LOCAL_DIR). "
                         "처리부를 실행한 폴더의 logs 폴더를 지정하세요.")
    source = Path(settings.local_dir).expanduser()
    if not source.is_dir():
        raise FetchError(f"로그 폴더가 없습니다: {source}")
    files = _log_files(source)
    if not files:
        raise FetchError(f"{source}에 처리부 로그(adp_*.log, adp_detection_*.csv)가 없습니다")
    result = FetchResult(destination)
    for index, path in enumerate(files, 1):
        target = destination / path.name
        report(f"복사 {index}/{len(files)}: {path.name}")
        if target.is_file() and target.stat().st_size == path.stat().st_size:
            result.unchanged.append(target)
            continue
        shutil.copy2(path, target)
        result.copied.append(target)
    return result


def scp_command(settings: FetchSettings, destination: Path, legacy_protocol: bool = True) -> List[str]:
    """보드 로그를 가져오는 scp 명령. 보드 dropbear는 SFTP가 없어 -O(옛 SCP 방식)가 필요하다."""
    remote = settings.remote_dir.rstrip("/") or "/"
    command = ["scp"]
    if legacy_protocol:
        command.append("-O")
    command += ["-P", str(settings.ssh_port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=5"]
    command += shlex.split(settings.ssh_options)
    # 원격 셸이 *를 펼친다. 두 종류를 한 번에 가져온다.
    command.append(f"{settings.ssh_target}:{remote}/adp_*")
    command.append(str(destination) + "/")
    return command


def _fetch_ssh(settings: FetchSettings, destination: Path, report: Progress) -> FetchResult:
    if shutil.which("scp") is None:
        raise FetchError("scp 명령이 없습니다 (openssh-client 설치 필요)")
    before = {path.name: path.stat().st_size for path in _log_files(destination)}
    report(f"{settings.ssh_target}:{settings.remote_dir} 에서 가져오는 중...")
    completed = _run(scp_command(settings, destination, legacy_protocol=True))
    if completed.returncode != 0 and "unknown option" in completed.stderr and "-- O" in completed.stderr:
        # OpenSSH 9.0보다 오래된 scp는 -O가 없고 원래 옛 방식을 쓴다.
        completed = _run(scp_command(settings, destination, legacy_protocol=False))
    if completed.returncode != 0:
        raise FetchError(_explain_ssh_error(completed.stderr, settings))
    result = FetchResult(destination)
    for path in _log_files(destination):
        if before.get(path.name) == path.stat().st_size:
            result.unchanged.append(path)
        else:
            result.copied.append(path)
    if not result.files:
        raise FetchError(f"{settings.remote_dir}에 처리부 로그가 없습니다")
    return result


def _run(command: List[str]) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=SSH_TIMEOUT_SECONDS,
                              stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired as error:
        raise FetchError(f"scp가 {SSH_TIMEOUT_SECONDS}초 안에 끝나지 않았습니다") from error


def _explain_ssh_error(stderr: str, settings: FetchSettings) -> str:
    text = stderr.strip().splitlines()
    last = text[-1] if text else "알 수 없는 오류"
    lower = stderr.lower()
    if "permission denied" in lower:
        hint = ("보드에 공개키가 등록되어 있지 않습니다. 비밀번호는 버튼에서 입력할 수 없으므로 "
                "ssh-copy-id 등으로 키를 먼저 등록하세요. 보드가 재부팅되면 등록이 사라질 수 있습니다.")
    elif "no such file" in lower:
        hint = f"보드에 {settings.remote_dir} 폴더나 로그 파일이 없습니다. 처리부를 run_adp.sh로 실행했는지 확인하세요."
    elif "timed out" in lower or "no route" in lower or "unreachable" in lower or "refused" in lower:
        hint = f"보드({settings.ssh_target})에 접속할 수 없습니다. 이더넷 연결과 노트북 IP 설정을 확인하세요."
    else:
        hint = "scp 출력을 확인하세요."
    return f"{hint}\n(scp: {last})"


def main(arguments: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m aesa_monitor.io.log_fetch",
                                     description="처리부 로그(adp_*.log, adp_detection_*.csv)를 가져온다")
    parser.add_argument("--settings", type=Path, default=DEFAULT_SETTINGS_PATH,
                        help="모니터 설정 파일 (기본값: 프로젝트 폴더의 monitor.env)")
    parser.add_argument("--dest", type=Path, required=True, help="저장할 폴더")
    parser.add_argument("--source", choices=("local", "ssh"), help="가져오는 방식")
    parser.add_argument("--local-dir", help="local: 처리부 logs 폴더")
    parser.add_argument("--target", help="ssh: 사용자@주소 (예: root@192.168.10.20)")
    parser.add_argument("--port", type=int, help="ssh: 포트")
    parser.add_argument("--remote-dir", help="ssh: 보드의 로그 폴더 (기본값 /tmp/aesa/logs)")
    options = parser.parse_args(arguments)

    settings = load_settings(options.settings).fetch
    changes = {
        "source": options.source, "local_dir": options.local_dir, "ssh_target": options.target,
        "ssh_port": options.port, "remote_dir": options.remote_dir,
    }
    from dataclasses import replace
    settings = replace(settings, **{key: value for key, value in changes.items() if value is not None})
    try:
        result = fetch_logs(settings, options.dest, progress=lambda text: print(text, flush=True))
    except FetchError as error:
        print(f"로그 가져오기 실패: {error}", file=sys.stderr)
        return 1
    print(result.describe())
    return 0


if __name__ == "__main__":
    sys.exit(main())
