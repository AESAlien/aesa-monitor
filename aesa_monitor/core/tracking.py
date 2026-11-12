"""확인·추적 빔의 예측 방향, 모의기 탐지, 처리부 갱신 결과를 사이클 단위로 묶는다.

처리부는 다음 빔 방향을 트랙 상태에서 예측해 빔 송신 명령에 담고, 모의기는 그 빔에 대한
탐지 결과를 돌려준다. 처리부는 탐지 중 하나를 연관해 트랙을 갱신하고 트랙 목록을 보낸다.
모니터는 이 세 메시지를 빔 ID(= 트랙 ID)와 송신 시각으로 맞춰 한 번의 추적 계산을 재구성한다.

처리부 내부 값(예측 거리, 공분산, NDS 점수)은 받지 않으므로 다음은 모니터의 추정이다.
- 예측 위치: 빔 방향만 있으므로 각 탐지의 거리에서 빔 축 위의 점을 예측 위치로 본다.
- 연관된 탐지: 예측 방향에 가장 가까운 탐지를 고른다. 처리부는 공분산을 반영한 NDS로 고른다.
"""

from __future__ import annotations

import math
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Iterable, List, Optional, Tuple

from aesa_monitor.core.geometry import BeamFrame, Vector, distance_m, position_m
from aesa_monitor.core.protocol import Beam, BeamType, Detection, Plot, Track

# 탐지를 기다리는 요청 빔을 이 개수까지만 보관한다.
PENDING_BEAM_LIMIT = 2000
# 트랙마다 보관하는 사이클 수
CYCLE_HISTORY_SIZE = 300
# 트랙 목록이 올 때마다 결과를 다시 판정하는 최근 사이클 수.
# 탐지 복사본이 그 탐지를 반영한 트랙 목록보다 먼저 올 수 있으므로 판정을 한동안 열어 둔다.
RESOLVE_WINDOW = 64


class Outcome:
    PENDING = "pending"
    UPDATED = "updated"
    MISSED = "missed"
    ENDED = "ended"


OUTCOME_NAMES = {
    Outcome.PENDING: "대기",
    Outcome.UPDATED: "갱신",
    Outcome.MISSED: "미탐",
    Outcome.ENDED: "종료",
}


def track_position(track: Track) -> Vector:
    return position_m(track.slant_range_km, track.azimuth_deg, track.elevation_deg)


@dataclass
class PlotView:
    """예측 방향 기준으로 본 탐지 하나."""

    plot: Plot
    position: Vector
    cross_m: float = math.nan
    up_m: float = math.nan
    normalized: Tuple[float, float] = (math.nan, math.nan)

    @property
    def angle_error_m(self) -> float:
        """예측 방향과 탐지 사이의 수직 거리. 각도 차이를 탐지 거리로 환산한 값이다."""
        return math.hypot(self.cross_m, self.up_m)

    @property
    def beam_offset(self) -> float:
        """빔 중심에서 가장자리까지를 1로 둔 거리. 1을 넘으면 모의기 기준 빔 밖이다."""
        return math.hypot(*self.normalized)


@dataclass
class TrackingCycle:
    track_id: int
    beam_type: int
    time_ms: int
    predicted: Optional[Tuple[float, float]]
    beam_width: Optional[Tuple[float, float]]
    plots: List[PlotView]
    previous_estimate: Optional[Track]
    selected_index: Optional[int] = None
    outcome: str = Outcome.PENDING
    estimate: Optional[Track] = None
    next_beam: Optional[Beam] = None

    @property
    def selected(self) -> Optional[PlotView]:
        return None if self.selected_index is None else self.plots[self.selected_index]

    @property
    def frame(self) -> Optional[BeamFrame]:
        return None if self.predicted is None else BeamFrame(*self.predicted)

    @property
    def elapsed_ms(self) -> Optional[int]:
        """예측에 쓴 시간 간격. 직전 추정 시각에서 이번 빔 송신 시각까지다."""
        if self.previous_estimate is None:
            return None
        return self.time_ms - self.previous_estimate.timestamp_ms

    def estimate_offset(self) -> Optional[Tuple[float, float, float]]:
        """갱신 결과를 예측 방향 단면 좌표로 나타낸다."""
        if self.estimate is None or self.frame is None:
            return None
        return self.frame.offset(track_position(self.estimate))

    def correction_m(self) -> Optional[float]:
        """연관된 탐지와 갱신 결과 사이의 거리. 필터가 탐지를 얼마나 덜 믿었는지 보여준다."""
        selected = self.selected
        if self.estimate is None or selected is None:
            return None
        return distance_m(track_position(self.estimate), selected.position)


@dataclass
class TrackSummary:
    track_id: int
    cycles: int
    updated: int
    missed: int
    consecutive_misses: int
    rms_angle_error_m: Optional[float]
    max_beam_offset: Optional[float]
    ended: bool


class TrackingHistory:
    def __init__(self, cycle_history_size: Optional[int] = CYCLE_HISTORY_SIZE) -> None:
        self._cycle_history_size = cycle_history_size
        self._pending_beams: "OrderedDict[Tuple[int, int], Beam]" = OrderedDict()
        self._cycles: Dict[int, Deque[TrackingCycle]] = {}
        self._recent: Deque[TrackingCycle] = deque(maxlen=RESOLVE_WINDOW)
        self.latest_tracks: Dict[int, Track] = {}
        self.search_detections = 0
        self.search_plots = 0

    def clear(self) -> None:
        self._pending_beams.clear()
        self._cycles.clear()
        self._recent.clear()
        self.latest_tracks = {}
        self.search_detections = 0
        self.search_plots = 0

    # 입력

    def apply_beam(self, beam: Beam) -> None:
        if beam.beam_type == BeamType.SEARCH:
            return
        self._pending_beams[(beam.beam_id, beam.timestamp_ms)] = beam
        while len(self._pending_beams) > PENDING_BEAM_LIMIT:
            self._pending_beams.popitem(last=False)

        # 이전 사이클 뒤에 나간 같은 트랙의 빔이 그 사이클 결과로 계산한 다음 예측이다.
        cycles = self._cycles.get(beam.beam_id)
        if cycles:
            last = cycles[-1]
            if last.next_beam is None and last.time_ms < beam.timestamp_ms:
                last.next_beam = beam

    def apply_detection(self, detection: Detection) -> Optional[TrackingCycle]:
        if detection.beam_type == BeamType.SEARCH:
            self.search_detections += 1
            self.search_plots += len(detection.plots)
            return None

        beam = self._pending_beams.pop((detection.beam_id, detection.timestamp_ms), None)
        cycle = TrackingCycle(
            track_id=detection.beam_id,
            beam_type=detection.beam_type,
            time_ms=detection.timestamp_ms,
            predicted=None if beam is None else (beam.azimuth_deg, beam.elevation_deg),
            beam_width=None if beam is None else (beam.azimuth_width_deg, beam.elevation_width_deg),
            plots=[self._plot_view(plot, beam) for plot in detection.plots],
            previous_estimate=self.latest_tracks.get(detection.beam_id),
        )
        cycle.selected_index = self._select(cycle.plots)
        self._cycles.setdefault(cycle.track_id, deque(maxlen=self._cycle_history_size)).append(cycle)
        self._recent.append(cycle)
        return cycle

    def apply_tracks(self, tracks: Iterable[Track]) -> None:
        latest = {track.track_id: track for track in tracks}
        for cycle in self._recent:
            if cycle.outcome == Outcome.UPDATED:
                continue
            track = latest.get(cycle.track_id)
            if track is None:
                # 트랙이 사라졌으면 마지막 사이클에서 종료된 것이다. 앞선 미탐은 그대로 둔다.
                if cycle is self._cycles[cycle.track_id][-1]:
                    cycle.outcome = Outcome.ENDED
                elif cycle.outcome == Outcome.PENDING:
                    cycle.outcome = Outcome.MISSED
            elif track.timestamp_ms == cycle.time_ms:
                cycle.outcome = Outcome.UPDATED
                cycle.estimate = track
            else:
                cycle.outcome = Outcome.MISSED
        self.latest_tracks = latest

    @staticmethod
    def _plot_view(plot: Plot, beam: Optional[Beam]) -> PlotView:
        position = position_m(plot.slant_range_km, plot.azimuth_deg, plot.elevation_deg)
        view = PlotView(plot, position)
        if beam is None:
            return view
        frame = BeamFrame(beam.azimuth_deg, beam.elevation_deg)
        view.cross_m, view.up_m, _ = frame.offset(position)
        view.normalized = frame.normalized_offset(
            plot.azimuth_deg, plot.elevation_deg, beam.azimuth_width_deg, beam.elevation_width_deg
        )
        return view

    @staticmethod
    def _select(plots: List[PlotView]) -> Optional[int]:
        if not plots:
            return None
        if any(math.isnan(plot.angle_error_m) for plot in plots):
            return 0
        return min(range(len(plots)), key=lambda index: plots[index].angle_error_m)

    # 조회

    def track_ids(self) -> List[int]:
        return sorted(self._cycles)

    def cycles(self, track_id: int) -> List[TrackingCycle]:
        return list(self._cycles.get(track_id, ()))

    def all_cycles(self) -> List[TrackingCycle]:
        result = [cycle for cycles in self._cycles.values() for cycle in cycles]
        result.sort(key=lambda cycle: cycle.time_ms)
        return result

    def summary(self, track_id: int) -> TrackSummary:
        cycles = self.cycles(track_id)
        errors = [cycle.selected.angle_error_m for cycle in cycles
                  if cycle.selected is not None and not math.isnan(cycle.selected.angle_error_m)]
        offsets = [plot.beam_offset for cycle in cycles for plot in cycle.plots
                   if not math.isnan(plot.beam_offset)]
        consecutive = 0
        for cycle in reversed(cycles):
            if cycle.outcome != Outcome.MISSED:
                break
            consecutive += 1
        return TrackSummary(
            track_id=track_id,
            cycles=len(cycles),
            updated=sum(cycle.outcome == Outcome.UPDATED for cycle in cycles),
            missed=sum(cycle.outcome == Outcome.MISSED for cycle in cycles),
            consecutive_misses=consecutive,
            rms_angle_error_m=math.sqrt(sum(e * e for e in errors) / len(errors)) if errors else None,
            max_beam_offset=max(offsets) if offsets else None,
            ended=bool(cycles) and cycles[-1].outcome == Outcome.ENDED,
        )
