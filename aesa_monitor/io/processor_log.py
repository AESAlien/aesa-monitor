"""처리부 로그 파일 읽기.

처리부(aesa-processor)는 실행할 때마다 두 파일을 만든다.
    logs/adp_YYYYMMDD_HHMMSS_mmm.log            메시지 로그 (spdlog, 시각은 UTC)
    logs/adp_detection_YYYYMMDD_HHMMSS_mmm.csv  탐지 CSV (DetectionEventCsvLogger)
파일 이름의 시각은 처리부 시작 시각(로컬 시간)이다.

메시지 로그의 시각은 벽시계(UTC)이고, 탐지 CSV와 모니터 녹화의 시각은 처리부 시각(시작 후 ms)이다.
메시지 로그를 처리부 시각으로 바꾸려고 main 로거의 "Six worker threads started" 줄을 처리부 시각 0으로 본다.
main은 이 줄을 쓴 직후 빔 시각의 기준점(started)을 잡으므로 차이는 수 ms 이내다(추정).
보드 시계가 맞지 않아도 같은 파일 안의 시각 차이만 쓰므로 영향이 없다.
"""

from __future__ import annotations

import csv
import datetime
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

LINE_PATTERN = re.compile(
    r"^(?P<time>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3})Z "
    r"\[(?P<logger>[^\]]+)\] \[(?P<level>[^\]]+)\] \[thread=(?P<thread>\d+)\] (?P<text>.*)$"
)
MESSAGE_LOG_NAME = re.compile(r"^adp_(?P<stamp>\d{8}_\d{6}_\d{3})(?:_\d+)?\.log$")
DETECTION_LOG_NAME = re.compile(r"^adp_detection_(?P<stamp>\d{8}_\d{6}_\d{3})(?:_\d+)?\.csv$")
ORIGIN_TEXTS = ("Six worker threads started", "Application starting")
# 메시지 로그와 탐지 CSV는 같은 실행에서 몇 ms 차이로 만들어진다.
PAIR_TOLERANCE_SECONDS = 5.0

CATEGORY_OPERATION = "운용"
CATEGORY_ATTITUDE = "자세"
CATEGORY_REJECTED = "요청 거부"
CATEGORY_PROBLEM = "경고·오류"
CATEGORY_LIFECYCLE = "시작·종료"
CATEGORY_OTHER = "기타"
CATEGORIES = (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM,
              CATEGORY_LIFECYCLE, CATEGORY_OTHER)

DETECTION_HEADER = ("beamType,requestId,transmitTime_ms,plotCount,"
                    "plotIndex,slantRange_m,azimuth_deg,elevation_deg,dopplerVelocity_mps,power_db")
BEAM_TYPE_NAMES = {"SEARCH": 1, "CONFIRMATION": 2, "TRACKING": 3}


@dataclass(frozen=True)
class LogEvent:
    wall_utc: datetime.datetime
    processor_ms: Optional[float]
    logger: str
    level: str
    text: str
    category: str
    details: Dict[str, str] = field(default_factory=dict, compare=False)

    @property
    def summary(self) -> str:
        """화면에 보여줄 한국어 요약."""
        details = self.details
        if self.category == CATEGORY_OPERATION:
            return f"운용 {details.get('state', '?')}"
        if self.category == CATEGORY_ATTITUDE:
            action = details.get("action", "")
            yaw = details.get("yaw", "?")
            if action == "applied":
                return f"자세 반영 (yaw {yaw})"
            if "OFF" in action:
                return f"자세 무시: 운용 OFF 중 수신 (yaw {yaw})"
            return f"자세 무시: 이미 설정됨 (yaw {yaw})"
        if self.category == CATEGORY_REJECTED:
            return f"요청 빔 거부 #{details.get('requestId', '?')} ({details.get('status', '?')})"
        return f"[{self.logger}] {self.text}"


@dataclass
class MessageLog:
    path: Path
    events: List[LogEvent]
    origin_utc: Optional[datetime.datetime]
    skipped_lines: int = 0

    def counts(self) -> Dict[str, int]:
        result = {category: 0 for category in CATEGORIES}
        for event in self.events:
            result[event.category] += 1
        return result


@dataclass(frozen=True)
class CsvPlot:
    slant_range_m: float
    azimuth_deg: float
    elevation_deg: float
    doppler_mps: float
    power_db: float


@dataclass
class CsvDetection:
    """탐지 CSV에서 빔 하나(같은 종류, requestId, 시각)에 해당하는 행 묶음."""

    beam_type: int
    request_id: int
    time_ms: int
    plots: List[CsvPlot]


@dataclass
class DetectionLog:
    path: Path
    detections: List[CsvDetection]
    skipped_lines: int = 0


@dataclass
class ProcessorRun:
    """처리부 한 번 실행에서 나온 로그 묶음."""

    stamp: str
    message_log: Optional[Path] = None
    detection_log: Optional[Path] = None

    @property
    def started(self) -> Optional[datetime.datetime]:
        return parse_stamp(self.stamp)

    @property
    def label(self) -> str:
        started = self.started
        parts = [started.strftime("%Y-%m-%d %H:%M:%S") if started else self.stamp]
        kinds = []
        if self.message_log:
            kinds.append("메시지 로그")
        if self.detection_log:
            kinds.append("탐지 CSV")
        return f"{parts[0]} ({', '.join(kinds)})"


def parse_stamp(stamp: str) -> Optional[datetime.datetime]:
    try:
        return datetime.datetime.strptime(stamp, "%Y%m%d_%H%M%S_%f")
    except ValueError:
        return None


def group_runs(paths: Iterable[Path]) -> List[ProcessorRun]:
    """로그 파일들을 실행 단위로 묶는다. 시작 시각 순서로 반환한다."""
    messages: List[Tuple[datetime.datetime, str, Path]] = []
    detections: List[Tuple[datetime.datetime, str, Path]] = []
    for path in paths:
        path = Path(path)
        for pattern, target in ((MESSAGE_LOG_NAME, messages), (DETECTION_LOG_NAME, detections)):
            match = pattern.match(path.name)
            if match:
                started = parse_stamp(match.group("stamp"))
                if started is not None:
                    target.append((started, match.group("stamp"), path))
    runs: List[ProcessorRun] = []
    used = set()
    for started, stamp, path in sorted(messages):
        run = ProcessorRun(stamp, message_log=path)
        best = None
        for index, (other_started, _, other_path) in enumerate(detections):
            if index in used:
                continue
            difference = abs((other_started - started).total_seconds())
            if difference <= PAIR_TOLERANCE_SECONDS and (best is None or difference < best[0]):
                best = (difference, index, other_path)
        if best is not None:
            used.add(best[1])
            run.detection_log = best[2]
        runs.append(run)
    for index, (started, stamp, path) in enumerate(detections):
        if index not in used:
            runs.append(ProcessorRun(stamp, detection_log=path))
    runs.sort(key=lambda run: run.started or datetime.datetime.min)
    return runs


def _classify(logger: str, level: str, text: str) -> Tuple[str, Dict[str, str]]:
    if logger == "BeamEvent":
        match = re.match(r"Operation state changed: (\w+)", text)
        if match:
            return CATEGORY_OPERATION, {"state": match.group(1)}
        match = re.match(r"Attitude (.+): roll=([^,]+), pitch=([^,]+), yaw=(.+)$", text)
        if match:
            return CATEGORY_ATTITUDE, {"action": match.group(1), "roll": match.group(2),
                                       "pitch": match.group(3), "yaw": match.group(4)}
        match = re.match(r"Beam request rejected: status=(\w+), requestId=(\d+)", text)
        if match:
            return CATEGORY_REJECTED, {"status": match.group(1), "requestId": match.group(2)}
    if level in ("warning", "warn", "error", "critical"):
        return CATEGORY_PROBLEM, {}
    if logger == "main":
        return CATEGORY_LIFECYCLE, {}
    return CATEGORY_OTHER, {}


def read_message_log(path: Path) -> MessageLog:
    raw: List[Tuple[datetime.datetime, str, str, str]] = []
    skipped = 0
    with open(path, encoding="utf-8", errors="replace") as file:
        for line in file:
            line = line.rstrip("\r\n")
            if not line:
                continue
            match = LINE_PATTERN.match(line)
            if not match:
                # 쓰는 중이던 마지막 줄이나 형식이 다른 줄은 건너뛴다.
                skipped += 1
                continue
            wall = datetime.datetime.strptime(match.group("time"), "%Y-%m-%dT%H:%M:%S.%f")
            raw.append((wall, match.group("logger"), match.group("level").lower(), match.group("text")))

    origin = None
    for origin_text in ORIGIN_TEXTS:
        origin = next((wall for wall, logger, _, text in raw
                       if logger == "main" and text.startswith(origin_text)), None)
        if origin is not None:
            break
    events = []
    for wall, logger, level, text in raw:
        category, details = _classify(logger, level, text)
        processor_ms = None if origin is None else (wall - origin).total_seconds() * 1000.0
        events.append(LogEvent(wall, processor_ms, logger, level, text, category, details))
    return MessageLog(Path(path), events, origin, skipped)


def read_detection_log(path: Path) -> DetectionLog:
    detections: List[CsvDetection] = []
    skipped = 0
    current: Optional[CsvDetection] = None
    with open(path, encoding="utf-8", errors="replace", newline="") as file:
        reader = csv.reader(file)
        header = next(reader, None)
        if header is None:
            return DetectionLog(Path(path), [], 0)
        if ",".join(header).strip() != DETECTION_HEADER:
            raise ValueError(f"{path}: 탐지 CSV 헤더가 다릅니다")
        for row in reader:
            if len(row) != 10:
                skipped += 1
                continue
            try:
                beam_type = BEAM_TYPE_NAMES.get(row[0], 0) or int(row[0])
                request_id = int(row[1])
                time_ms = int(row[2])
                plot_count = int(row[3])
                plot = None
                if row[4] != "":
                    plot = CsvPlot(float(row[5]), float(row[6]), float(row[7]), float(row[8]), float(row[9]))
            except ValueError:
                skipped += 1
                continue
            key = (beam_type, request_id, time_ms)
            if current is None or (current.beam_type, current.request_id, current.time_ms) != key:
                current = CsvDetection(beam_type, request_id, time_ms, [])
                detections.append(current)
            if plot is not None and len(current.plots) < plot_count:
                current.plots.append(plot)
    return DetectionLog(Path(path), detections, skipped)
