"""분석할 세션 불러오기: 녹화 파일과 처리부 로그를 한 번에 연다.

열 수 있는 것
- 세션 폴더: monitor.aesarec + processor_logs/ (모니터가 만든 폴더)
- 아무 폴더: *.aesarec, adp_*.log, adp_detection_*.csv, logs/ 아래 파일
- 녹화 파일 하나, 처리부 로그 파일 하나
- 캡처 파일 하나(*.pcap, *.pcapng, *.cap): 통신 캡처로 받은 것처럼 재생한다

녹화가 없고 탐지 CSV가 있으면 처리부 로그로 재생 이벤트를 만들어(io/log_replay.py) 녹화처럼 재생한다.
이때 빔 방향·트랙 목록·레이더 정면은 모니터 추정이고, 통계는 탐지 CSV에서 바로 계산한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from aesa_monitor.core.config import BeamGeometry, TrackingParameters
from aesa_monitor.core.problems import BAD, CATEGORY_LOG, WARN, Problem, level_counts, scan_events
from aesa_monitor.core.stats import StatsReport, compute_stats, records_from_csv, records_from_events
from aesa_monitor.io.bookmarks import Bookmark, BookmarkError, bookmark_path, load_bookmarks, save_bookmarks
from aesa_monitor.io.capture import is_capture_file, read_capture_file
from aesa_monitor.io.log_replay import LogRecording, build_log_recording
from aesa_monitor.io.pcap import PcapError
from aesa_monitor.io.processor_log import (
    CATEGORY_PROBLEM, CATEGORY_REJECTED, DetectionLog, MessageLog, ProcessorRun, group_runs, read_detection_log,
    read_message_log,
)
from aesa_monitor.io.recording import Recording, RecordingError, find_session_files, read_recording
from aesa_monitor.io.replay import ReplayPlayer


@dataclass
class Session:
    path: Path
    parameters: TrackingParameters
    beam: BeamGeometry = field(default_factory=BeamGeometry)
    recording: Optional[Recording] = None
    player: Optional[ReplayPlayer] = None
    runs: List[ProcessorRun] = field(default_factory=list)
    run: Optional[ProcessorRun] = None
    message_log: Optional[MessageLog] = None
    detection_log: Optional[DetectionLog] = None
    report: Optional[StatsReport] = None
    errors: List[str] = field(default_factory=list)
    bookmarks: List[Bookmark] = field(default_factory=list)
    # 녹화를 처음부터 다시 계산해 찾은 문제(core/problems.py). 녹화가 없으면 비어 있다.
    recording_problems: List[Problem] = field(default_factory=list)
    _problems: Optional[List[Problem]] = field(default=None, repr=False)

    @property
    def is_log_replay(self) -> bool:
        """녹화 없이 처리부 로그로 만든 재생인지."""
        return isinstance(self.recording, LogRecording)

    def compute_problems(self) -> None:
        if self.player is None:
            self.recording_problems = []
        else:
            self.recording_problems = scan_events(self.player.events, self.parameters, self.player.segment_at)
        self._problems = None

    def log_problems(self) -> List[Problem]:
        """고른 처리부 실행의 메시지 로그에서 요청 거부·경고·오류를 문제로 바꾼다.

        로그는 처리부 시각만 있어 녹화 위치는 그 시각에 처음 닿은 곳으로 정한다(녹화 중 처리부가 다시 시작했으면
        앞 실행으로 갈 수 있음, 한계).
        """
        if self.message_log is None:
            return []
        # 로그 실행 수와 녹화의 처리부 실행 구간 수가 같으면 순서대로 짝짓는다(추정: 둘 다 시간 순서이고
        # 녹화가 모든 실행을 담았다고 봄). 다르면 구간을 모르므로 처리부 시각에 처음 닿은 곳으로 간다.
        segment: Optional[int] = None
        if self.player is not None and self.run in self.runs and len(self.runs) == self.player.segment_count:
            segment = self.runs.index(self.run)
        problems = []
        for event in self.message_log.events:
            if event.category not in (CATEGORY_REJECTED, CATEGORY_PROBLEM) or event.processor_ms is None:
                continue
            processor_ms = int(event.processor_ms)
            request = event.details.get("requestId", "")
            start_s = (self.player.time_for_processor_ms(processor_ms, segment) if self.player is not None
                       else None)
            problems.append(Problem(
                "log", CATEGORY_LOG, BAD if event.level in ("error", "critical") else WARN, event.summary,
                f"처리부 로그 {event.level}" + ("" if segment is not None or self.player is None or
                                              self.player.segment_count <= 1 else " · 녹화 구간 추정 불가"),
                processor_ms, processor_ms, start_s, start_s,
                int(request) if request.isdigit() else None, open=False,
                segment=None if start_s is None else self.player.segment_at(start_s)))
        return problems

    def problems(self) -> List[Problem]:
        """녹화에서 찾은 문제와 처리부 로그 문제를 녹화 순서(없으면 처리부 시각)로 합친 목록."""
        if self._problems is None:
            merged = self.recording_problems + self.log_problems()
            self._problems = sorted(merged, key=lambda problem: (
                problem.start_s if problem.start_s is not None else (problem.start_ms or 0) / 1000,
                problem.start_ms or 0))
        return self._problems

    @property
    def bookmark_file(self) -> Path:
        return bookmark_path(self.path)

    def load_bookmarks(self) -> None:
        try:
            self.bookmarks = load_bookmarks(self.bookmark_file)
        except BookmarkError as error:
            self.bookmarks = []
            self.errors.append(f"북마크: {error}")

    def save_bookmarks(self) -> None:
        """북마크를 세션 옆 파일에 쓴다. 실패하면 OSError를 그대로 올린다(화면에서 알린다)."""
        save_bookmarks(self.bookmark_file, self.bookmarks)

    def select_run(self, index: Optional[int]) -> None:
        """처리부 실행(로그 묶음)을 고르고 통계를 다시 계산한다."""
        self._problems = None
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
        if self.recording is None or self.is_log_replay:
            self._build_log_replay()
        self.compute()

    def _build_log_replay(self) -> None:
        """녹화가 없으면 고른 실행의 탐지 CSV(와 메시지 로그)로 재생을 만든다. 실행을 바꾸면 다시 만든다."""
        self.recording = None
        self.player = None
        if self.detection_log is None or not self.detection_log.detections:
            return
        self.recording = build_log_recording(self.detection_log, self.message_log, self.beam, self.parameters)
        self.player = ReplayPlayer(self.recording)
        self.compute_problems()

    def compute(self) -> None:
        if self.player is not None and not self.is_log_replay:
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
        if self.recording is not None and not self.is_log_replay:
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
        elif self.is_log_replay:
            lines.append("녹화: 없음 — 처리부 로그로 재생 (빔 방향·트랙 위치는 탐지 CSV로 만든 모니터 추정, 통신 기록 없음)")
            assert isinstance(self.recording, LogRecording)
            if self.recording.fit is not None:
                lines.append(f"  {self.recording.fit.describe()}")
            lines.extend(f"  {note}" for note in self.recording.notes)
        else:
            lines.append("녹화: 없음 (재생은 할 수 없고 처리부 로그로만 분석)")
        if self.run is not None:
            lines.append(f"처리부 로그: {self.run.label} — 실행 {len(self.runs)}개 중 선택")
        else:
            lines.append("처리부 로그: 없음")
        if self.player is not None or self.message_log is not None:
            counts = level_counts(self.problems())
            lines.append(f"자동 기록 문제: 이상 {counts[BAD]}건 · 주의 {counts[WARN]}건 (분석 탭 [문제] 목록)")
        lines.extend(self.errors)
        return lines


def load_session(path: Path, parameters: TrackingParameters, beam: Optional[BeamGeometry] = None) -> Session:
    path = Path(path).expanduser()
    session = Session(path, parameters, beam or BeamGeometry())
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
        session.compute_problems()
        session.load_bookmarks()
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
    if not session.is_log_replay:  # 로그 재생은 select_run()에서 이미 계산했다
        session.compute_problems()
    session.load_bookmarks()
    return session
