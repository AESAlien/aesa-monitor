"""수신한 메시지를 누적하고 화면에 필요한 값을 계산한다.

모든 시간 계산은 처리부 시각(빔 송신 명령과 상태 메시지의 시각)을 기준으로 한다.
그래야 처리부 속도를 바꿔도 점유율과 탐색 프레임 시간이 같은 값으로 나온다.
연결 유지 시간, 수신 속도처럼 모니터 쪽 시간이 필요한 값은 clock으로 잰다.
실시간에서는 단조 시계, 녹화 재생에서는 재생 위치(녹화 시작 후 초)를 clock으로 쓴다.
"""

from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, List, Optional, Tuple

from aesa_monitor.core.events import (
    PEER_CONSOLE, PEER_NAMES, PEER_SIMULATOR, SOURCE_CAPTURE, SOURCE_COPY, ConnectionChanged, Event,
    PeerLinkChanged,
)
from aesa_monitor.core.frames import FrameCounter
from aesa_monitor.core.geometry import wrap_degrees
from aesa_monitor.core.protocol import (
    Beam, BeamType, Detection, MonitorStatus, OperatorControl, RadarAttitude, SimulatorAttitude, Track,
    TrackDeletion, TrackList,
)
from aesa_monitor.core.tracking import TrackingHistory
from aesa_monitor.core.traffic import MessageTraffic

BEAM_HISTORY_SIZE = 5000
# 이보다 크게 처리부 시각이 거꾸로 가면 처리부가 다시 시작했다고 보고 기록을 지운다.
TIME_RESET_THRESHOLD_MS = 1000
# 이 시간 동안 상태 메시지가 없으면 상태를 알 수 없다고 표시한다.
STATUS_TIMEOUT_SECONDS = 1.0
# 이 배율 이하이거나 정지 상태이면 자동 보기에서 상세 보기를 사용한다.
DETAIL_VIEW_TIME_SCALE = 0.25
# 처리부가 빔을 보내는 간격
BEAM_SLOT_MS = 10
# 확인 빔이 어느 탐색 탐지에서 나왔는지 찾을 때 보는 범위. 탐색 프레임 시간(약 1.9 s)보다 짧아야
# 앞 프레임의 탐지와 짝이 지어지지 않는다.
CONFIRMATION_SEARCH_WINDOW_MS = 1000
CONFIRMATION_MATCH_DEG = 0.5
EVENT_LOG_SIZE = 200
GAP_HISTORY_SIZE = 2000
# 보관할 탐색 프레임 수. 프레임 하나가 약 1.9 s이므로 1시간 남짓이다.
FRAME_HISTORY_SIZE = 2000


@dataclass
class SearchCell:
    azimuth_deg: float
    elevation_deg: float
    width_deg: float
    last_ms: int
    previous_ms: Optional[int] = None


@dataclass(frozen=True)
class LogEntry:
    """통신 탭에 보여주는 연결 이벤트. clock_s는 state의 clock 값이다."""

    clock_s: float
    processor_ms: Optional[int]
    text: str
    level: str  # "ok", "warn", "bad", "info"


@dataclass(frozen=True)
class ScheduleRow:
    """트랙별 요청 빔 일정 한 줄."""

    track_id: int
    last_type: int
    last_ms: int
    interval_ms: Optional[int]
    deviation_ms: Optional[int]
    next_ms: Optional[int]
    count: int
    active: bool


class ViewMode:
    AUTO = "auto"
    DETAIL = "detail"
    FLOW = "flow"


class MonitorState:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.connected = False
        self.connection_detail = "connecting"
        # 데이터 출처(복사본/캡처). ConnectionChanged가 알려준다.
        self.source = SOURCE_COPY
        # 캡처에서 본 처리부-상대 연결 상태. 복사본 수신에서는 상태 메시지(0xAD01)를 쓴다.
        self.peer_links: Dict[str, bool] = {}
        # 캡처에서 본 마지막 운용 명령(0xCA01)
        self.operator_control: Optional[OperatorControl] = None
        self.simulator_attitude: Optional[SimulatorAttitude] = None
        self.status: Optional[MonitorStatus] = None
        self._status_received_at: Optional[float] = None
        self.attitude: Optional[RadarAttitude] = None
        self.tracks: List[Track] = []
        self.beams: Deque[Beam] = deque(maxlen=BEAM_HISTORY_SIZE)
        self.search_cells: Dict[int, SearchCell] = {}
        self._azimuth_reference: Optional[float] = None
        self._last_search_interval_ms: Optional[int] = None
        self._latest_beam_ms: Optional[int] = None
        self.tracking = TrackingHistory()
        self.traffic = MessageTraffic(clock)
        self.connect_count = 0
        self.connected_since: Optional[float] = None
        self.event_log: Deque[LogEntry] = deque(maxlen=EVENT_LOG_SIZE)
        # 처리부 시각 순서로 (시각, 빠진 빔 칸 수)
        self.beam_gaps: Deque[Tuple[int, int]] = deque(maxlen=GAP_HISTORY_SIZE)
        # 플롯이 있었던 탐색 탐지: (시각, 방위, 고각)
        self._search_hits: Deque[Tuple[int, float, float]] = deque()
        # (확인 빔 시각, 탐색 탐지에서 확인 빔까지 걸린 시간)
        self.confirmation_delays: Deque[Tuple[int, int]] = deque(maxlen=200)
        self.processor_restarts = 0
        # 탐색 프레임별 빔 종류 수
        self.frames = FrameCounter(FRAME_HISTORY_SIZE)

    # 입력

    def apply(self, event: Event, received_at: Optional[float] = None) -> None:
        if isinstance(event, ConnectionChanged):
            self._apply_connection(event, received_at)
            return
        if isinstance(event, PeerLinkChanged):
            self._apply_peer_link(event, received_at)
            return
        self.traffic.record(event, received_at)
        if isinstance(event, Beam):
            self._apply_beam(event)
        elif isinstance(event, Detection):
            self._apply_detection(event)
        elif isinstance(event, TrackList):
            self.tracks = sorted(event.tracks, key=lambda track: track.track_id)
            self.tracking.apply_tracks(event.tracks)
        elif isinstance(event, RadarAttitude):
            if self.attitude is None or self.attitude != event:
                self._log(f"레이더 자세 수신 (yaw {event.yaw_deg:.2f}°)", "info", received_at)
            self.attitude = event
        elif isinstance(event, MonitorStatus):
            self._apply_status(event, received_at)
        elif isinstance(event, OperatorControl):
            if self.operator_control != event:
                self._log(f"운용 명령: {'운용(ON)' if event.running else f'정지(OFF, {event.status})'}",
                          "info" if event.running else "warn", received_at)
            self.operator_control = event
        elif isinstance(event, SimulatorAttitude):
            if self.simulator_attitude != event:
                self._log(f"모의기 자세 수신 (yaw {event.yaw_deg:.2f}°)", "info", received_at)
            self.simulator_attitude = event
        elif isinstance(event, TrackDeletion):
            self._log(f"트랙 삭제: {', '.join(f'#{track_id}' for track_id in event.track_ids) or '-'}",
                      "info", received_at)

    def _now(self, received_at: Optional[float]) -> float:
        return self.clock() if received_at is None else received_at

    def _log(self, text: str, level: str, received_at: Optional[float] = None) -> None:
        self.event_log.append(LogEntry(self._now(received_at), self.now_ms(), text, level))

    def _apply_connection(self, event: ConnectionChanged, received_at: Optional[float]) -> None:
        self.source = event.source
        capture = event.source == SOURCE_CAPTURE
        if event.connected and not self.connected:
            # 다시 연결된 처리부는 새로 시작했을 수 있으므로 이전 기록을 버린다.
            self.clear_history()
            self.traffic.clear()
            self.status = None
            self._status_received_at = None
            self.peer_links.clear()
            self.operator_control = None
            self.connect_count += 1
            self.connected_since = self._now(received_at)
            self._log("캡처 시작" if capture else "처리부에 연결됨", "ok", received_at)
        elif not event.connected:
            if self.connected or self.connect_count == 0:
                detail = f" ({event.detail})" if event.detail else ""
                self._log(f"{'캡처 중지' if capture else '처리부 연결 끊김'}{detail}", "bad", received_at)
            self.connected_since = None
        self.connected = event.connected
        self.connection_detail = event.detail

    def _apply_peer_link(self, event: PeerLinkChanged, received_at: Optional[float]) -> None:
        before = self.peer_links.get(event.peer)
        self.peer_links[event.peer] = event.connected
        if before != event.connected:
            name = PEER_NAMES.get(event.peer, event.peer)
            detail = f" ({event.detail})" if event.detail else ""
            self._log(f"{name} {'연결됨' if event.connected else '연결 끊김'}{detail}",
                      "ok" if event.connected else "bad", received_at)

    def _apply_status(self, status: MonitorStatus, received_at: Optional[float]) -> None:
        previous = self.status
        for name, before, after in (
            ("운용통제기", None if previous is None else previous.operator_console_connected,
             status.operator_console_connected),
            ("모의기", None if previous is None else previous.scenario_simulator_connected,
             status.scenario_simulator_connected),
        ):
            if before is None or before != after:
                self._log(f"{name} {'연결됨' if after else '연결 안 됨'}", "ok" if after else "bad", received_at)
        if previous is not None and previous.paused != status.paused:
            self._log("처리부 정지" if status.paused else "처리부 재개", "warn" if status.paused else "info",
                      received_at)
        self.status = status
        self._status_received_at = self._now(received_at)

    def clear_history(self) -> None:
        self.tracks = []
        self.beams.clear()
        self.search_cells.clear()
        self._azimuth_reference = None
        self._last_search_interval_ms = None
        self._latest_beam_ms = None
        self.tracking.clear()
        self.beam_gaps.clear()
        self._search_hits.clear()
        self.confirmation_delays.clear()
        self.frames.clear()

    def _apply_beam(self, beam: Beam) -> None:
        if (self._latest_beam_ms is not None and
                beam.timestamp_ms + TIME_RESET_THRESHOLD_MS < self._latest_beam_ms):
            self.clear_history()
            self.processor_restarts += 1
            self._log("처리부 시각이 되돌아감 (처리부 재시작으로 보고 기록을 지움)", "warn")

        if self._latest_beam_ms is not None:
            missed = (beam.timestamp_ms - self._latest_beam_ms) // BEAM_SLOT_MS - 1
            if missed > 0:
                self.beam_gaps.append((beam.timestamp_ms, int(missed)))

        self.beams.append(beam)
        self.tracking.apply_beam(beam)
        self.frames.add(beam.beam_type, beam.beam_id, beam.timestamp_ms)
        if self._latest_beam_ms is None or beam.timestamp_ms > self._latest_beam_ms:
            self._latest_beam_ms = beam.timestamp_ms

        if beam.beam_type == BeamType.SEARCH:
            self._apply_search_beam(beam)
        elif beam.beam_type == BeamType.CONFIRMATION:
            self._match_confirmation(beam)

    def _apply_detection(self, detection: Detection) -> None:
        self.tracking.apply_detection(detection)
        if detection.beam_type == BeamType.SEARCH:
            for plot in detection.plots:
                self._search_hits.append((detection.timestamp_ms, plot.azimuth_deg, plot.elevation_deg))
            while (self._search_hits and
                   self._search_hits[0][0] < detection.timestamp_ms - CONFIRMATION_SEARCH_WINDOW_MS):
                self._search_hits.popleft()

    def _match_confirmation(self, beam: Beam) -> None:
        """확인 빔 방향과 같은 탐색 탐지를 찾아 탐색 → 확인 지연을 기록한다."""
        # 겹친 이웃 탐색 칸이 같은 표적을 10 ms 뒤에 또 찾을 수 있다. 확인 요청은 처음 찾은 탐지에서
        # 나가므로 범위 안에서 가장 이른 탐지를 고른다(추정).
        best: Optional[Tuple[int, float, float]] = None
        for hit in self._search_hits:
            time_ms, azimuth, elevation = hit
            if time_ms >= beam.timestamp_ms or beam.timestamp_ms - time_ms > CONFIRMATION_SEARCH_WINDOW_MS:
                continue
            if (abs(wrap_degrees(azimuth - beam.azimuth_deg)) <= CONFIRMATION_MATCH_DEG and
                    abs(elevation - beam.elevation_deg) <= CONFIRMATION_MATCH_DEG):
                if best is None or time_ms < best[0]:
                    best = hit
        if best is not None:
            self.confirmation_delays.append((beam.timestamp_ms, beam.timestamp_ms - best[0]))

    def _apply_search_beam(self, beam: Beam) -> None:
        if self._azimuth_reference is None:
            self._azimuth_reference = beam.azimuth_deg

        cell = self.search_cells.get(beam.beam_id)
        if cell is None:
            self.search_cells[beam.beam_id] = SearchCell(
                self.unwrap_azimuth(beam.azimuth_deg),
                beam.elevation_deg,
                beam.azimuth_width_deg,
                beam.timestamp_ms,
            )
            return

        cell.azimuth_deg = self.unwrap_azimuth(beam.azimuth_deg)
        cell.elevation_deg = beam.elevation_deg
        cell.previous_ms = cell.last_ms
        cell.last_ms = beam.timestamp_ms
        self._last_search_interval_ms = cell.last_ms - cell.previous_ms

    def unwrap_azimuth(self, azimuth_deg: float) -> float:
        """첫 탐색 빔의 방위를 기준으로 ±180도 안에 들도록 방위를 펼친다.

        격자가 0도나 180도를 가로질러도 화면에서 끊기지 않게 한다.
        """
        if self._azimuth_reference is None:
            return azimuth_deg
        reference = self._azimuth_reference
        return reference + ((azimuth_deg - reference + 180.0) % 360.0) - 180.0

    # 조회

    def now_ms(self) -> Optional[int]:
        """화면에 표시할 현재 처리부 시각."""
        candidates = [value for value in (
            self._latest_beam_ms,
            self.status.processor_time_ms if self.status else None,
        ) if value is not None]
        return max(candidates) if candidates else None

    def status_is_fresh(self, now: Optional[float] = None) -> bool:
        if self._status_received_at is None:
            return False
        current = self.clock() if now is None else now
        return current - self._status_received_at <= STATUS_TIMEOUT_SECONDS

    def fresh_status(self) -> Optional[MonitorStatus]:
        """연결되어 있고 최근에 받은 상태. 아니면 None이다."""
        return self.status if self.connected and self.status_is_fresh() else None

    def peer_connected(self, peer: str) -> Optional[bool]:
        """처리부와 상대(운용통제기, 모의기)의 연결 상태. 모르면 None이다.

        상태 메시지(0xAD01)가 있으면 그것을, 없으면 캡처에서 본 TCP 연결 상태를 쓴다.
        """
        status = self.fresh_status()
        if status is not None:
            if peer == PEER_CONSOLE:
                return status.operator_console_connected
            if peer == PEER_SIMULATOR:
                return status.scenario_simulator_connected
            return None
        if self.connected and peer in self.peer_links:
            return self.peer_links[peer]
        return None

    def paused(self) -> Optional[bool]:
        """처리부가 정지 상태인지. 상태 메시지가 없으면 캡처한 운용 명령(0xCA01)으로 추정하고, 모르면 None."""
        status = self.fresh_status()
        if status is not None:
            return status.paused
        if self.connected and self.operator_control is not None:
            return not self.operator_control.running
        return None

    def time_scale(self) -> float:
        status = self.fresh_status()
        return status.time_scale if status is not None and status.time_scale > 0 else 1.0

    def recent_beams(self, window_ms: int) -> List[Beam]:
        """최근 window_ms 동안 송신한 빔을 오래된 순서로 반환한다."""
        now = self.now_ms()
        if now is None:
            return []
        start = now - window_ms
        result = []
        for beam in reversed(self.beams):
            if beam.timestamp_ms < start:
                break
            result.append(beam)
        result.reverse()
        return result

    def occupancy(self, window_ms: int) -> Dict[BeamType, float]:
        """최근 window_ms 동안 빔 종류별 비율을 반환한다. 빔이 없으면 모두 0이다."""
        beams = self.recent_beams(window_ms)
        counts = {beam_type: 0 for beam_type in BeamType}
        for beam in beams:
            if beam.beam_type in counts:
                counts[BeamType(beam.beam_type)] += 1
        total = sum(counts.values())
        if total == 0:
            return {beam_type: 0.0 for beam_type in BeamType}
        return {beam_type: count / total for beam_type, count in counts.items()}

    def search_sweep_ms(self) -> Optional[int]:
        """가장 최근에 다시 방문한 탐색 칸의 방문 간격. 탐색 프레임 시간(탐색 격자 전체를 한 번 도는 시간)에 해당한다."""
        return self._last_search_interval_ms

    def expected_sweep_ms(self, window_ms: int = 5000) -> Optional[float]:
        """격자 칸 수와 최근 탐색 점유율로 계산한 탐색 프레임 시간의 기대값.

        확인·추적 빔이 끼어든 만큼 탐색 프레임 시간이 길어진다.
        """
        if not self.search_cells:
            return None
        share = self.occupancy(window_ms)[BeamType.SEARCH]
        if share <= 0:
            return None
        return len(self.search_cells) * BEAM_SLOT_MS / share

    def missed_beam_slots(self, window_ms: int) -> int:
        """최근 window_ms 동안 10 ms 칸에 빔이 오지 않은 횟수.

        처리부가 늦었거나 모니터 큐가 넘쳐 빔 복사본이 버려진 경우다.
        """
        now = self.now_ms()
        if now is None:
            return 0
        return sum(missed for time_ms, missed in self.beam_gaps if time_ms >= now - window_ms)

    def latest_confirmation_delay(self) -> Optional[int]:
        return self.confirmation_delays[-1][1] if self.confirmation_delays else None

    def request_schedule(self, tracking_interval_ms: int) -> List[ScheduleRow]:
        """트랙(요청 ID)별 마지막 요청 빔, 직전 간격, 설정 주기와의 차이."""
        history: Dict[int, List[Beam]] = {}
        counts: Dict[int, int] = {}
        for beam in self.beams:
            if beam.beam_type == BeamType.SEARCH:
                continue
            counts[beam.beam_id] = counts.get(beam.beam_id, 0) + 1
            items = history.setdefault(beam.beam_id, [])
            items.append(beam)
            if len(items) > 2:
                items.pop(0)
        active = set(self.tracking.latest_tracks)
        rows = []
        for track_id in sorted(history):
            items = history[track_id]
            last = items[-1]
            interval = last.timestamp_ms - items[0].timestamp_ms if len(items) == 2 else None
            deviation = (interval - tracking_interval_ms
                         if interval is not None and last.beam_type == BeamType.TRACKING
                         and items[0].beam_type == BeamType.TRACKING else None)
            is_active = track_id in active
            rows.append(ScheduleRow(
                track_id, last.beam_type, last.timestamp_ms, interval, deviation,
                last.timestamp_ms + tracking_interval_ms if is_active else None,
                counts[track_id], is_active,
            ))
        return rows

    def max_period_deviation(self, tracking_interval_ms: int) -> Optional[int]:
        deviations = [abs(row.deviation_ms) for row in self.request_schedule(tracking_interval_ms)
                      if row.active and row.deviation_ms is not None]
        return max(deviations) if deviations else None

    def resolve_view_mode(self, selected: str) -> str:
        if selected != ViewMode.AUTO:
            return selected
        if self.status and (self.status.paused or self.status.time_scale <= DETAIL_VIEW_TIME_SCALE):
            return ViewMode.DETAIL
        return ViewMode.FLOW


def finite_or_none(value: Optional[float]) -> Optional[float]:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return value
