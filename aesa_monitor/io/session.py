"""분석할 세션 불러오기: 녹화 파일과 처리부 로그를 한 번에 연다.

열 수 있는 것
- 세션 폴더: monitor.aesarec + processor_logs/ (모니터가 만든 폴더)
- 아무 폴더: *.aesarec, adp_*.log, adp_detection_*.csv, logs/ 아래 파일
- 녹화 파일 하나, 처리부 로그 파일 하나
- 캡처 파일 하나(*.pcap, *.pcapng, *.cap): 통신 캡처로 받은 것처럼 재생한다
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.stats import StatsReport, compute_stats, records_from_csv, records_from_events
from aesa_monitor.io.capture import is_capture_file, read_capture_file
from aesa_monitor.io.pcap import PcapError
from aesa_monitor.io.processor_log import (
    DetectionLog, MessageLog, ProcessorRun, group_runs, read_detection_log, read_message_log,
)
from aesa_monitor.io.recording import Recording, RecordingError, find_session_files, read_recording
from aesa_monitor.io.replay import ReplayPlayer


@dataclass
class Session:
    path: Path
    parameters: TrackingParameters
    recording: Optional[Recording] = None
    player: Optional[ReplayPlayer] = None
    runs: List[ProcessorRun] = field(default_factory=list)
    run: Optional[ProcessorRun] = None
    message_log: Optional[MessageLog] = None
    detection_log: Optional[DetectionLog] = None
    report: Optional[StatsReport] = None
    errors: List[str] = field(default_factory=list)

    def select_run(self, index: Optional[int]) -> None:
        """처리부 실행(로그 묶음)을 고르고 통계를 다시 계산한다."""
        self.run = None if index is None or not self.runs else self.runs[index]
        self.message_log = None
        self.detection_log = None
        if self.run is not None:
            if self.run.message_log is not None:
                try:
                    self.message_log = read_message_log(self.run.message_log)
                except OSError as error:
                    self.errors.append(f"메시지 로그를 읽지 못함: {error}")
            if self.run.detection_log is not None:
                try:
                    self.detection_log = read_detection_log(self.run.detection_log)
                except (OSError, ValueError) as error:
                    self.errors.append(f"탐지 CSV를 읽지 못함: {error}")
        self.compute()

    def compute(self) -> None:
        if self.player is not None:
            records, extra = records_from_events(self.player.events)
            kind = "캡처 파일" if is_capture_file(self.recording.path) else "모니터 녹화"
            source = f"{kind} {self.recording.path.name}"
            self.report = compute_stats(records, self.parameters, source, extra, self.message_log)
        elif self.detection_log is not None:
            records = records_from_csv(self.detection_log.detections)
            source = f"처리부 탐지 CSV {self.detection_log.path.name} (빔 방향·트랙 목록 없음)"
            self.report = compute_stats(records, self.parameters, source, None, self.message_log)
        else:
            self.report = None

    def describe(self) -> List[str]:
        lines = [f"세션: {self.path}"]
        if self.recording is not None:
            truncated = " (끝이 잘림: 녹화 중이었거나 비정상 종료)" if self.recording.truncated else ""
            lines.append(f"녹화: {self.recording.path.name}, {self.recording.duration_s:.1f} s, "
                         f"레코드 {len(self.recording.records)}개{truncated}")
            if self.player is not None and self.player.decode_errors:
                lines.append(f"  해석하지 못한 프레임 {self.player.decode_errors}개")
            meta = self.recording.meta
            if meta.get("source") == "capture":
                if "packets" in meta:
                    lines.append(f"  통신 캡처: 패킷 {meta['packets']}개, TCP {meta['tcp_segments']}개, "
                                 f"놓친 구간 {meta['gaps']}, 건너뛴 바이트 {meta['skipped_bytes']}, "
                                 f"뺀 모니터 연결 {meta['excluded_monitor_connections']}")
                elif "capture_command" in meta:
                    lines.append(f"  통신 캡처: {meta['capture_command']}")
        else:
            lines.append("녹화: 없음 (재생은 할 수 없고 처리부 로그로만 분석)")
        if self.run is not None:
            lines.append(f"처리부 로그: {self.run.label} — 실행 {len(self.runs)}개 중 선택")
        else:
            lines.append("처리부 로그: 없음")
        lines.extend(self.errors)
        return lines


def load_session(path: Path, parameters: TrackingParameters) -> Session:
    path = Path(path).expanduser()
    session = Session(path, parameters)
    recording_path: Optional[Path] = None
    log_files: List[Path] = []
    if path.is_dir():
        recording_path, log_files = find_session_files(path)
    elif path.suffix == ".aesarec":
        recording_path = path
        _, log_files = find_session_files(path.parent)
    elif is_capture_file(path) and path.is_file():
        try:
            session.recording = read_capture_file(path)
            session.player = ReplayPlayer(session.recording)
        except (OSError, PcapError) as error:
            session.errors.append(f"캡처 파일을 읽지 못함: {error}")
        session.compute()
        return session
    elif path.is_file():
        log_files = [path]
        # 같은 실행의 짝 파일도 함께 찾는다.
        _, siblings = find_session_files(path.parent)
        log_files = siblings or log_files
    else:
        session.errors.append(f"열 수 없는 경로: {path}")
        return session

    if recording_path is not None:
        try:
            session.recording = read_recording(recording_path)
            session.player = ReplayPlayer(session.recording)
        except (OSError, RecordingError) as error:
            session.errors.append(f"녹화를 읽지 못함: {error}")
    session.runs = group_runs(log_files)
    if session.runs:
        index = len(session.runs) - 1
        if path.is_file() and path.suffix != ".aesarec":
            for position, run in enumerate(session.runs):
                if path in (run.message_log, run.detection_log):
                    index = position
        session.select_run(index)
    else:
        session.compute()
    return session
