"""모니터 접속 설정과 화면 설명에 쓰는 추적 설정.

데이터 처리부와 같은 .env 파일에서 서버 주소와 모니터 주소를 읽는다.
처리부는 수락한 연결을 원격 주소와 포트로 구분하므로, 모니터는
COMM_MONITOR_ADDRESS:COMM_MONITOR_PORT에 바인딩한 뒤 접속해야 한다.
추적 설정(TARGET_*)은 추적 탭에서 처리부의 계산 기준을 보여줄 때만 쓰며,
없으면 .env.example과 같은 기본값을 쓴다.
운용통제기·모의기 주소(COMM_OPERATOR_CONSOLE_*, COMM_SCENARIO_SIMULATOR_*)는 통신 캡처에서
연결의 상대를 정할 때만 쓰며, 없어도 된다.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

Endpoint = Tuple[str, int]


@dataclass(frozen=True)
class TrackingParameters:
    measurement_standard_deviation_m: float = 7.0
    position_nds_threshold: float = 11.345
    confirmation_delay_ms: int = 100
    tracking_interval_ms: int = 1000
    max_consecutive_tracking_misses: int = 3
    max_association_speed_mps: float = 1000.0
    max_initial_doppler_difference_mps: float = 30.0
    process_acceleration_standard_deviation_mps2: float = 10.0


# TrackingParameters 필드와 .env 키의 대응
TRACKING_KEYS = {
    "measurement_standard_deviation_m": "TARGET_MEASUREMENT_STANDARD_DEVIATION_M",
    "position_nds_threshold": "TARGET_POSITION_NDS_THRESHOLD",
    "confirmation_delay_ms": "TARGET_CONFIRMATION_DELAY_MS",
    "tracking_interval_ms": "TARGET_TRACKING_INTERVAL_MS",
    "max_consecutive_tracking_misses": "TARGET_MAX_CONSECUTIVE_TRACKING_MISSES",
    "max_association_speed_mps": "TARGET_MAX_ASSOCIATION_SPEED_MPS",
    "max_initial_doppler_difference_mps": "TARGET_MAX_INITIAL_DOPPLER_DIFFERENCE_MPS",
    "process_acceleration_standard_deviation_mps2": "TARGET_PROCESS_ACCELERATION_STANDARD_DEVIATION_MPS2",
}


@dataclass(frozen=True)
class MonitorConfig:
    server: Endpoint
    local: Endpoint
    tracking: TrackingParameters = TrackingParameters()
    console: Optional[Endpoint] = None
    simulator: Optional[Endpoint] = None


class ConfigError(ValueError):
    """설정 파일이나 인자가 잘못되었다."""


def read_env_file(path: Path) -> Dict[str, str]:
    """KEY=VALUE 형식의 .env 파일을 읽는다. 주석과 따옴표를 처리한다."""
    values: Dict[str, str] = {}
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigError(f"{path}:{line_number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        value = value.strip()
        if value[:1] in ("'", '"') and value[-1:] == value[:1] and len(value) >= 2:
            value = value[1:-1]
        elif "#" in value:
            value = value.split("#", 1)[0].strip()
        values[key.strip()] = value
    return values


def parse_endpoint(text: str) -> Endpoint:
    """'주소:포트' 문자열을 엔드포인트로 변환한다."""
    address, separator, port_text = text.rpartition(":")
    if not separator or not address:
        raise ConfigError(f"endpoint must be ADDRESS:PORT: {text}")
    return address, _parse_port(port_text, text)


def _parse_port(text: str, source: str) -> int:
    try:
        port = int(text)
    except ValueError as error:
        raise ConfigError(f"invalid port: {source}") from error
    if not 0 < port < 65536:
        raise ConfigError(f"port out of range: {source}")
    return port


def read_tracking_parameters(values: Dict[str, str]) -> TrackingParameters:
    """값이 있는 TARGET_* 키만 읽고 나머지는 기본값을 쓴다."""
    defaults = TrackingParameters()
    fields = {}
    for name, key in TRACKING_KEYS.items():
        text = values.get(key, "")
        if not text:
            continue
        value_type = type(getattr(defaults, name))
        try:
            fields[name] = value_type(text)
        except ValueError as error:
            raise ConfigError(f"invalid {key}: {text}") from error
    return TrackingParameters(**fields)


def _require(values: Dict[str, str], key: str) -> str:
    value = values.get(key, "")
    if not value:
        raise ConfigError(f"missing {key}")
    return value


def load_config(
    env_path: Optional[Path],
    server: Optional[str] = None,
    local: Optional[str] = None,
) -> MonitorConfig:
    """.env 파일 값을 읽고 명령행에서 지정한 값으로 덮어쓴다."""
    values: Dict[str, str] = {}
    if env_path is not None:
        if not env_path.is_file():
            raise ConfigError(f"env file not found: {env_path}")
        values = read_env_file(env_path)

    if server is not None:
        server_endpoint = parse_endpoint(server)
    else:
        server_endpoint = (
            _require(values, "COMM_SERVER_ADDRESS"),
            _parse_port(_require(values, "COMM_SERVER_PORT"), "COMM_SERVER_PORT"),
        )

    if local is not None:
        local_endpoint = parse_endpoint(local)
    else:
        local_endpoint = (
            _require(values, "COMM_MONITOR_ADDRESS"),
            _parse_port(_require(values, "COMM_MONITOR_PORT"), "COMM_MONITOR_PORT"),
        )

    return MonitorConfig(server_endpoint, local_endpoint, read_tracking_parameters(values),
                         _optional_endpoint(values, "COMM_OPERATOR_CONSOLE"),
                         _optional_endpoint(values, "COMM_SCENARIO_SIMULATOR"))


def _optional_endpoint(values: Dict[str, str], prefix: str) -> Optional[Endpoint]:
    address = values.get(f"{prefix}_ADDRESS", "")
    port = values.get(f"{prefix}_PORT", "")
    if not address or not port:
        return None
    return address, _parse_port(port, f"{prefix}_PORT")
