"""모니터 자체 설정(monitor.env).

처리부 .env에는 처리부 설정만 두고, 모니터만 쓰는 값(데이터 출처, 녹화 폴더, 로그 가져오기, 글자 크기)은
이 파일에 둔다. 형식은 처리부 .env와 같은 KEY=VALUE다. 파일이 없으면 기본값을 쓴다.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Dict, Optional

from aesa_monitor.core.config import ConfigError, read_env_file

PROJECT_DIRECTORY = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_PATH = PROJECT_DIRECTORY / "monitor.env"
DEFAULT_SSH_OPTIONS = (
    "-o HostKeyAlgorithms=+ssh-rsa -o PubkeyAcceptedAlgorithms=+ssh-rsa "
    "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
)
SOURCES = ("local", "ssh")
# 데이터 출처. copy: 처리부가 보내는 복사본을 받는다. capture: 처리부·모의기·운용통제기 사이 패킷을 캡처한다.
DATA_SOURCES = ("copy", "capture")
# 표준 출력으로 pcap을 내보내는 캡처 명령. {port}는 처리부 서버 포트로 바뀐다.
# --immediate-mode가 없으면 libpcap이 패킷을 묶어서 넘겨 화면이 늦고 뭉쳐서 갱신된다.
DEFAULT_CAPTURE_COMMAND = "tcpdump -i any --immediate-mode -U -s 0 -w - tcp port {port}"


@dataclass(frozen=True)
class FetchSettings:
    """처리부 로그를 어디서 가져올지.

    local: 처리부를 이 PC에서 실행할 때. local_dir의 로그 파일을 복사한다.
    ssh: 처리부를 보드에서 실행할 때. scp로 remote_dir의 로그 파일을 가져온다.
    """

    source: str = "local"
    local_dir: str = ""
    ssh_target: str = "root@192.168.10.20"
    ssh_port: int = 22
    remote_dir: str = "/tmp/aesa/logs"
    ssh_options: str = DEFAULT_SSH_OPTIONS


@dataclass(frozen=True)
class MonitorSettings:
    data_source: str = "copy"
    capture_command: str = DEFAULT_CAPTURE_COMMAND
    record_root: str = str(Path.home() / "aesa-records")
    font_size: int = 12
    auto_record: bool = False
    fetch: FetchSettings = field(default_factory=FetchSettings)

    @property
    def record_path(self) -> Path:
        return Path(self.record_root).expanduser()


KEYS = {
    "MONITOR_SOURCE": ("data_source", str),
    "MONITOR_CAPTURE_COMMAND": ("capture_command", str),
    "MONITOR_RECORD_DIR": ("record_root", str),
    "MONITOR_FONT_SIZE": ("font_size", int),
    "MONITOR_AUTO_RECORD": ("auto_record", bool),
}
FETCH_KEYS = {
    "MONITOR_LOG_SOURCE": ("source", str),
    "MONITOR_LOG_LOCAL_DIR": ("local_dir", str),
    "MONITOR_LOG_SSH_TARGET": ("ssh_target", str),
    "MONITOR_LOG_SSH_PORT": ("ssh_port", int),
    "MONITOR_LOG_REMOTE_DIR": ("remote_dir", str),
    "MONITOR_LOG_SSH_OPTIONS": ("ssh_options", str),
}


def _convert(key: str, text: str, kind):
    if kind is bool:
        if text.lower() in ("1", "true", "yes", "on"):
            return True
        if text.lower() in ("0", "false", "no", "off", ""):
            return False
        raise ConfigError(f"invalid {key}: {text}")
    try:
        return kind(text)
    except ValueError as error:
        raise ConfigError(f"invalid {key}: {text}") from error


def load_settings(path: Optional[Path] = None) -> MonitorSettings:
    path = DEFAULT_SETTINGS_PATH if path is None else Path(path)
    if not path.is_file():
        return MonitorSettings()
    values = read_env_file(path)
    main: Dict[str, object] = {}
    fetch: Dict[str, object] = {}
    for key, (name, kind) in KEYS.items():
        if values.get(key, "") != "":
            main[name] = _convert(key, values[key], kind)
    for key, (name, kind) in FETCH_KEYS.items():
        if key in values and (values[key] != "" or name == "local_dir"):
            fetch[name] = _convert(key, values[key], kind)
    fetch_settings = FetchSettings(**fetch)
    if fetch_settings.source not in SOURCES:
        raise ConfigError(f"MONITOR_LOG_SOURCE must be one of {', '.join(SOURCES)}")
    if main.get("data_source", "copy") not in DATA_SOURCES:
        raise ConfigError(f"MONITOR_SOURCE must be one of {', '.join(DATA_SOURCES)}")
    if not 8 <= int(main.get("font_size", 12)) <= 32:
        raise ConfigError("MONITOR_FONT_SIZE must be 8..32")
    return MonitorSettings(**main, fetch=fetch_settings)


def _format(value) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    text = str(value)
    if any(character in text for character in ("#", " ")) and not text.startswith('"'):
        return f'"{text}"'
    return text


def settings_values(settings: MonitorSettings) -> Dict[str, str]:
    values = {key: _format(getattr(settings, name)) for key, (name, _) in KEYS.items()}
    values.update({key: _format(getattr(settings.fetch, name)) for key, (name, _) in FETCH_KEYS.items()})
    return values


def save_settings(settings: MonitorSettings, path: Optional[Path] = None) -> Path:
    """설정을 저장한다. 파일에 있던 주석과 다른 키는 그대로 둔다."""
    path = DEFAULT_SETTINGS_PATH if path is None else Path(path)
    values = settings_values(settings)
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    written = set()
    output = []
    for line in lines:
        stripped = line.strip()
        key = stripped.split("=", 1)[0].strip() if "=" in stripped and not stripped.startswith("#") else None
        if key in values:
            output.append(f"{key}={values[key]}")
            written.add(key)
        else:
            output.append(line)
    for key, value in values.items():
        if key not in written:
            output.append(f"{key}={value}")
    path.write_text("\n".join(output) + "\n", encoding="utf-8")
    return path


def with_fetch(settings: MonitorSettings, **changes) -> MonitorSettings:
    return replace(settings, fetch=replace(settings.fetch, **changes))

