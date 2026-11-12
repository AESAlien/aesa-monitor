"""모니터가 받은 프레임의 녹화와 읽기.

녹화 파일(monitor.aesarec)은 처리부가 보낸 프레임을 해석하지 않고 그대로 저장한다.
그래서 재생할 때 실시간과 같은 해석 코드(protocol.decode)와 같은 화면을 쓸 수 있다.

파일 구조 (리틀 엔디언)
    "AESAREC1" | uint32 메타 길이 | 메타(JSON, UTF-8)
    레코드 반복: uint8 종류 | float64 녹화 시작 후 초 | uint32 길이 | 내용
        종류 1 = 프레임(ICD 메시지 그대로), 2 = 처리부 연결, 3 = 처리부 연결 끊김(내용은 사유),
        4 = 처리부-상대 연결 상태(통신 캡처, 내용은 "상대|0 또는 1|사유")
    메타의 "source"는 데이터 출처(copy: 복사본 수신, capture: 통신 캡처)다. 없으면 copy다.
    종류 4는 나중에 추가했다. 이전 판은 모르는 종류를 건너뛰므로 같은 형식(AESAREC1)을 유지한다.

세션 폴더: <녹화 폴더>/<YYYYMMDD_HHMMSS>/
    monitor.aesarec      모니터 녹화
    processor_logs/      처리부에서 가져온 로그(adp_*.log, adp_detection_*.csv)
"""

from __future__ import annotations

import datetime
import json
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Callable, Dict, List, Optional, Tuple

from aesa_monitor import __version__
from aesa_monitor.core.events import SOURCE_COPY, ConnectionChanged, Event, PeerLinkChanged
from aesa_monitor.core.protocol import ProtocolError, decode

MAGIC = b"AESAREC1"
META_LENGTH = struct.Struct("<I")
RECORD_HEADER = struct.Struct("<BdI")
KIND_FRAME = 1
KIND_CONNECTED = 2
KIND_DISCONNECTED = 3
KIND_PEER_LINK = 4

RECORDING_NAME = "monitor.aesarec"
PROCESSOR_LOG_DIRECTORY = "processor_logs"
FLUSH_INTERVAL_SECONDS = 0.5


def default_record_root() -> Path:
    return Path.home() / "aesa-records"


def new_session_directory(root: Path, now: Optional[datetime.datetime] = None) -> Path:
    """녹화 폴더 아래에 시각 이름의 세션 폴더를 만든다. 이름이 겹치면 _1, _2를 붙인다."""
    stamp = (now or datetime.datetime.now()).strftime("%Y%m%d_%H%M%S")
    root.mkdir(parents=True, exist_ok=True)
    index = 0
    while True:
        name = stamp if index == 0 else f"{stamp}_{index}"
        path = root / name
        try:
            path.mkdir()
            return path
        except FileExistsError:
            index += 1


class Recorder:
    """수신 스레드에서 호출해도 되는 녹화기."""

    def __init__(self, path: Path, meta: Optional[Dict] = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self.path = path
        self._clock = clock
        self._lock = threading.Lock()
        self._started = clock()
        self._last_flush = self._started
        self.frames = 0
        self.bytes_written = 0
        info = {
            "format": 1,
            "created": datetime.datetime.now().isoformat(timespec="seconds"),
            "monitor_version": __version__,
        }
        info.update(meta or {})
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: Optional[BinaryIO] = open(path, "wb")
        encoded = json.dumps(info, ensure_ascii=False).encode("utf-8")
        self._write_raw(MAGIC + META_LENGTH.pack(len(encoded)) + encoded)

    @property
    def elapsed(self) -> float:
        return self._clock() - self._started

    @property
    def is_open(self) -> bool:
        return self._file is not None

    def _write_raw(self, data: bytes) -> None:
        assert self._file is not None
        self._file.write(data)
        self.bytes_written += len(data)

    def _write_record(self, kind: int, payload: bytes) -> None:
        with self._lock:
            if self._file is None:
                return
            now = self._clock()
            self._write_raw(RECORD_HEADER.pack(kind, now - self._started, len(payload)) + payload)
            if now - self._last_flush >= FLUSH_INTERVAL_SECONDS:
                self._file.flush()
                self._last_flush = now

    def write_frame(self, frame: bytes) -> None:
        self._write_record(KIND_FRAME, frame)
        self.frames += 1

    def write_connection(self, connected: bool, detail: str = "") -> None:
        self._write_record(KIND_CONNECTED if connected else KIND_DISCONNECTED, detail.encode("utf-8"))

    def write_peer_link(self, peer: str, connected: bool, detail: str = "") -> None:
        self._write_record(KIND_PEER_LINK, f"{peer}|{int(connected)}|{detail}".encode("utf-8"))

    def flush(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.flush()

    def close(self) -> None:
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None


@dataclass(frozen=True)
class Record:
    time_s: float
    kind: int
    payload: bytes


@dataclass
class Recording:
    path: Path
    meta: Dict
    records: List[Record]
    truncated: bool = False

    @property
    def duration_s(self) -> float:
        return self.records[-1].time_s if self.records else 0.0

    def events(self) -> Tuple[List[Tuple[float, Event]], int]:
        """레코드를 (녹화 시각, 이벤트)로 바꾼다. 해석할 수 없는 프레임 수를 함께 반환한다."""
        result: List[Tuple[float, Event]] = []
        errors = 0
        source = str(self.meta.get("source", SOURCE_COPY))
        for record in self.records:
            if record.kind == KIND_FRAME:
                try:
                    result.append((record.time_s, decode(record.payload)))
                except ProtocolError:
                    errors += 1
            elif record.kind == KIND_CONNECTED:
                result.append((record.time_s, ConnectionChanged(True, source=source)))
            elif record.kind == KIND_DISCONNECTED:
                result.append((record.time_s, ConnectionChanged(
                    False, record.payload.decode("utf-8", "replace"), source)))
            elif record.kind == KIND_PEER_LINK:
                peer, _, rest = record.payload.decode("utf-8", "replace").partition("|")
                connected, _, detail = rest.partition("|")
                result.append((record.time_s, PeerLinkChanged(peer, connected == "1", detail)))
        return result, errors


class RecordingError(ValueError):
    """녹화 파일 형식이 맞지 않는다."""


def read_recording(path: Path) -> Recording:
    """녹화 파일을 읽는다. 녹화 중인 파일처럼 끝이 잘려 있으면 완전한 레코드까지만 읽는다."""
    data = Path(path).read_bytes()
    if not data.startswith(MAGIC):
        raise RecordingError(f"{path}: 모니터 녹화 파일이 아닙니다")
    offset = len(MAGIC)
    if len(data) < offset + META_LENGTH.size:
        raise RecordingError(f"{path}: 메타 정보가 없습니다")
    (meta_length,) = META_LENGTH.unpack_from(data, offset)
    offset += META_LENGTH.size
    try:
        meta = json.loads(data[offset:offset + meta_length].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RecordingError(f"{path}: 메타 정보를 읽을 수 없습니다") from error
    offset += meta_length

    records: List[Record] = []
    truncated = False
    while offset < len(data):
        if len(data) - offset < RECORD_HEADER.size:
            truncated = True
            break
        kind, time_s, length = RECORD_HEADER.unpack_from(data, offset)
        start = offset + RECORD_HEADER.size
        if len(data) - start < length:
            truncated = True
            break
        records.append(Record(time_s, kind, data[start:start + length]))
        offset = start + length
    return Recording(Path(path), meta, records, truncated)


def find_session_files(directory: Path) -> Tuple[Optional[Path], List[Path]]:
    """세션 폴더(또는 아무 폴더)에서 녹화 파일과 처리부 로그 파일을 찾는다."""
    directory = Path(directory)
    recording = directory / RECORDING_NAME
    if not recording.is_file():
        candidates = sorted(directory.glob("*.aesarec"))
        recording = candidates[-1] if candidates else None
    log_files: List[Path] = []
    for folder in (directory / PROCESSOR_LOG_DIRECTORY, directory, directory / "logs"):
        if folder.is_dir():
            log_files.extend(sorted(folder.glob("adp_*.log")))
            log_files.extend(sorted(folder.glob("adp_detection_*.csv")))
    unique = []
    for path in log_files:
        if path not in unique:
            unique.append(path)
    return recording, unique
