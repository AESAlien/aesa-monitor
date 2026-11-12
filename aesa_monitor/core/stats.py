"""녹화나 처리부 탐지 CSV 전체를 한 번에 훑어 만드는 통계 요약.

두 입력을 같은 BeamRecord 목록으로 바꾼 뒤 같은 계산을 한다.
- 녹화: 빔 방향, 탐지, 트랙 목록이 모두 있다. 빔-탐지 차이와 갱신 여부까지 계산한다.
- 탐지 CSV만: 빔 방향과 트랙 목록이 없다. 방향이 필요한 값은 플롯 위치로 대신하거나 비워 둔다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.events import ConnectionChanged, Event
from aesa_monitor.core.geometry import wrap_degrees
from aesa_monitor.core.protocol import Beam, BeamType, Detection, TrackList
from aesa_monitor.core.state import BEAM_SLOT_MS, TIME_RESET_THRESHOLD_MS
from aesa_monitor.core.tracking import Outcome, TrackingHistory
from aesa_monitor.core.verdict import (
    BAD, CONFIRMATION_TOLERANCE_MS, IDLE, OK, PERIOD_TOLERANCE_MS, SWEEP_TOLERANCE, WARN,
)

CONFIRMATION_WINDOW_MS = 1000
DIRECTION_MATCH_DEG = 0.5
PLOT_MATCH_DEG = 1.0

# (거리 km, 방위, 고각, 도플러)
PlotTuple = Tuple[float, float, float, float]


@dataclass
class BeamRecord:
    time_ms: int
    beam_type: int
    beam_id: int
    azimuth_deg: Optional[float] = None
    elevation_deg: Optional[float] = None
    plots: Optional[List[PlotTuple]] = None   # None이면 탐지 결과를 받지 못함
    outcome: Optional[str] = None             # 녹화에서만: 갱신/미탐/종료
    angle_error_m: Optional[float] = None     # 녹화에서만: 빔 축과 연관 탐지의 수직 거리
    segment: int = 0                           # 처리부가 다시 시작할 때마다 1 증가


@dataclass
class StatItem:
    group: str
    name: str
    value: str
    reference: str = ""
    level: str = IDLE
    samples: List[float] = field(default_factory=list)
    unit: str = ""
    note: str = ""


@dataclass
class TrackStat:
    track_id: int
    first_ms: int
    last_ms: int
    has_confirmation: bool
    confirmed: bool
    tracking_beams: int
    misses: int
    max_consecutive_misses: int
    rms_angle_error_m: Optional[float]
    ended: bool

    @property
    def lifetime_s(self) -> float:
        return (self.last_ms - self.first_ms) / 1000.0


@dataclass
class StatsReport:
    source: str
    records: int
    span_ms: Tuple[int, int]
    items: List[StatItem]
    tracks: List[TrackStat]


# 입력 변환

def records_from_events(events: Iterable[Tuple[float, Event]]) -> Tuple[List[BeamRecord], Dict[str, int]]:
    """녹화 이벤트를 BeamRecord로 바꾼다. 연결 끊김 수 등 부가 정보를 함께 반환한다."""
    history = TrackingHistory(cycle_history_size=None)
    records: List[BeamRecord] = []
    pending: Dict[Tuple[int, int], BeamRecord] = {}
    extra = {"disconnects": 0, "restarts": 0}
    segment = 0
    latest: Optional[int] = None
    connected = False
    drop_pending = False
    for _, event in events:
        if isinstance(event, ConnectionChanged):
            if connected and not event.connected:
                # 녹화 마지막의 끊김(모니터 종료)은 세지 않도록 다음 이벤트가 올 때 센다.
                drop_pending = True
            connected = event.connected
            continue
        if drop_pending:
            extra["disconnects"] += 1
            drop_pending = False
        if isinstance(event, Beam):
            if latest is not None and event.timestamp_ms + TIME_RESET_THRESHOLD_MS < latest:
                # 처리부가 다시 시작했다. 이전 구간의 사이클 판정을 옮겨 두고 새로 시작한다.
                _apply_outcomes(records, history, segment)
                segment += 1
                extra["restarts"] += 1
                pending.clear()
                history = TrackingHistory(cycle_history_size=None)
                latest = None
            latest = event.timestamp_ms if latest is None else max(latest, event.timestamp_ms)
            record = BeamRecord(event.timestamp_ms, event.beam_type, event.beam_id,
                                event.azimuth_deg, event.elevation_deg, segment=segment)
            records.append(record)
            pending[(event.beam_id, event.timestamp_ms)] = record
            if len(pending) > 5000:
                pending.pop(next(iter(pending)))
            history.apply_beam(event)
        elif isinstance(event, Detection):
            record = pending.pop((event.beam_id, event.timestamp_ms), None)
            if record is not None:
                record.plots = [(plot.slant_range_km, plot.azimuth_deg, plot.elevation_deg, plot.doppler_mps)
                                for plot in event.plots]
            history.apply_detection(event)
        elif isinstance(event, TrackList):
            history.apply_tracks(event.tracks)
    # 트랙 목록이 올 때마다 판정이 갱신되므로 구간 끝에서 한 번에 옮긴다.
    _apply_outcomes(records, history, segment)
    return records, extra


def _apply_outcomes(records: List[BeamRecord], history: TrackingHistory, segment: int) -> None:
    cycles = {(cycle.track_id, cycle.time_ms): cycle for cycle in history.all_cycles()}
    for record in records:
        if record.segment != segment or record.beam_type == BeamType.SEARCH:
            continue
        cycle = cycles.get((record.beam_id, record.time_ms))
        if cycle is not None:
            record.outcome = cycle.outcome
            selected = cycle.selected
            if selected is not None and not math.isnan(selected.angle_error_m):
                record.angle_error_m = selected.angle_error_m


def records_from_csv(detections) -> List[BeamRecord]:
    """처리부 탐지 CSV(processor_log.CsvDetection 목록)를 BeamRecord로 바꾼다."""
    records = []
    segment = 0
    latest: Optional[int] = None
    for detection in detections:
        if latest is not None and detection.time_ms + TIME_RESET_THRESHOLD_MS < latest:
            segment += 1
        latest = detection.time_ms if latest is None else max(latest, detection.time_ms)
        plots = [(plot.slant_range_m / 1000.0, plot.azimuth_deg, plot.elevation_deg, plot.doppler_mps)
                 for plot in detection.plots]
        records.append(BeamRecord(detection.time_ms, detection.beam_type, detection.request_id,
                                  plots=plots, segment=segment))
    return records


# 계산

def _grade(difference: float, limits: Tuple[float, float]) -> str:
    if difference <= limits[0]:
        return OK
    if difference <= limits[1]:
        return WARN
    return BAD


def _summary(values: Sequence[float], unit: str, digits: int = 0) -> str:
    if not values:
        return "-"
    mean = sum(values) / len(values)
    return (f"평균 {mean:.{digits}f}{unit} (최소 {min(values):.{digits}f}, 최대 {max(values):.{digits}f}, "
            f"{len(values)}개)")


def _rms(values: Sequence[float]) -> Optional[float]:
    return math.sqrt(sum(value * value for value in values) / len(values)) if values else None


def _by_segment(records: Sequence[BeamRecord]) -> Dict[int, List[BeamRecord]]:
    result: Dict[int, List[BeamRecord]] = {}
    for record in records:
        result.setdefault(record.segment, []).append(record)
    return result


def _confirmation_delays(records: Sequence[BeamRecord]) -> List[float]:
    delays = []
    for segment in _by_segment(records).values():
        hits: List[Tuple[int, float, float]] = []
        for record in segment:
            if record.beam_type == BeamType.SEARCH and record.plots:
                hits.extend((record.time_ms, plot[1], plot[2]) for plot in record.plots)
                continue
            if record.beam_type != BeamType.CONFIRMATION:
                continue
            if record.azimuth_deg is not None:
                target = (record.azimuth_deg, record.elevation_deg)
                tolerance = DIRECTION_MATCH_DEG
            elif record.plots:
                target = (record.plots[0][1], record.plots[0][2])
                tolerance = PLOT_MATCH_DEG
            else:
                continue
            # 겹친 이웃 칸이 같은 표적을 또 찾을 수 있으므로 범위 안에서 가장 이른 탐지를 고른다(추정).
            best = None
            for time_ms, azimuth, elevation in reversed(hits):
                if time_ms >= record.time_ms:
                    continue
                if record.time_ms - time_ms > CONFIRMATION_WINDOW_MS:
                    break
                if (abs(wrap_degrees(azimuth - target[0])) <= tolerance and
                        abs(elevation - target[1]) <= tolerance):
                    best = time_ms
            if best is not None:
                delays.append(float(record.time_ms - best))
    return delays


def _track_stats(records: Sequence[BeamRecord], end_ms: int, interval_ms: int,
                 miss_limit: int) -> List[TrackStat]:
    by_id: Dict[Tuple[int, int], List[BeamRecord]] = {}
    for record in records:
        if record.beam_type in (BeamType.CONFIRMATION, BeamType.TRACKING):
            by_id.setdefault((record.segment, record.beam_id), []).append(record)
    result = []
    for (segment, track_id), items in sorted(by_id.items()):
        tracking = [item for item in items if item.beam_type == BeamType.TRACKING]
        misses = 0
        run = 0
        longest = 0
        for item in tracking:
            missed = (item.outcome == Outcome.MISSED) if item.outcome is not None else (item.plots == [])
            if missed:
                misses += 1
                run += 1
                longest = max(longest, run)
            else:
                run = 0
        errors = [item.angle_error_m for item in items if item.angle_error_m is not None]
        last = items[-1].time_ms
        segment_end = max(record.time_ms for record in records if record.segment == segment)
        # 마지막 요청 빔 뒤 추적 주기의 2배 넘게 빔이 없으면 종료로 본다.
        ended = segment_end - last > interval_ms * 2 or segment_end < end_ms - interval_ms * 2
        has_confirmation = any(item.beam_type == BeamType.CONFIRMATION for item in items)
        result.append(TrackStat(track_id, items[0].time_ms, last, has_confirmation, bool(tracking),
                                len(tracking), misses,
                                longest, _rms(errors), ended))
    return result


def compute_stats(records: Sequence[BeamRecord], parameters: TrackingParameters, source: str,
                  extra: Optional[Dict[str, int]] = None, message_log=None) -> StatsReport:
    records = sorted(records, key=lambda record: (record.segment, record.time_ms))
    items: List[StatItem] = []
    if not records:
        return StatsReport(source, 0, (0, 0), [StatItem("기록", "빔", "없음")], [])
    start, end = records[0].time_ms, records[-1].time_ms
    has_direction = any(record.azimuth_deg is not None for record in records)
    counts = {beam_type: sum(record.beam_type == beam_type for record in records) for beam_type in BeamType}
    total = sum(counts.values())

    group = "기록"
    items.append(StatItem(group, "입력", source))
    items.append(StatItem(group, "처리부 시각 구간", f"{start / 1000:.1f} ~ {end / 1000:.1f} s "
                                                   f"({(end - start) / 1000:.1f} s)"))
    items.append(StatItem(group, "빔 수", f"{total}개 (탐색 {counts[BeamType.SEARCH]}, "
                                         f"확인 {counts[BeamType.CONFIRMATION]}, 추적 {counts[BeamType.TRACKING]})"))
    if extra:
        if extra.get("restarts"):
            items.append(StatItem(group, "처리부 재시작", f"{extra['restarts']}회", level=WARN,
                                  note="처리부 시각이 되돌아간 횟수. 구간별로 따로 계산한다"))

    group = "빔 스케줄"
    shares = {beam_type: counts[beam_type] / total for beam_type in BeamType}
    items.append(StatItem(group, "빔 점유율", " / ".join(
        f"{name} {shares[beam_type] * 100:.1f}%" for beam_type, name in
        ((BeamType.SEARCH, "탐색"), (BeamType.CONFIRMATION, "확인"), (BeamType.TRACKING, "추적")))))

    intervals: List[float] = []
    for segment in _by_segment(records).values():
        times = sorted(record.time_ms for record in segment)
        intervals.extend(float(b - a) for a, b in zip(times, times[1:]))
    gaps = [value for value in intervals if value > BEAM_SLOT_MS]
    missed_slots = int(sum(value // BEAM_SLOT_MS - 1 for value in gaps))
    label = "연속 빔 간격" if has_direction else "연속 탐지 간격"
    items.append(StatItem(group, label, _summary(intervals, " ms"), f"{BEAM_SLOT_MS} ms",
                          _grade(missed_slots, (0, max(5, total // 1000))), intervals, "ms",
                          note=f"빠진 칸 {missed_slots}개 (처리부 지연, 모니터 큐 누락 또는 응답 없는 빔)"))

    sweeps: List[float] = []
    for segment in _by_segment(records).values():
        last_seen: Dict[int, int] = {}
        for record in segment:
            if record.beam_type != BeamType.SEARCH:
                continue
            if record.beam_id in last_seen:
                sweeps.append(float(record.time_ms - last_seen[record.beam_id]))
            last_seen[record.beam_id] = record.time_ms
    cells = len({record.beam_id for record in records if record.beam_type == BeamType.SEARCH})
    expected_sweep = cells * BEAM_SLOT_MS / shares[BeamType.SEARCH] if shares[BeamType.SEARCH] > 0 else None
    level = IDLE
    if sweeps and expected_sweep:
        mean = sum(sweeps) / len(sweeps)
        level = _grade(abs(mean - expected_sweep) / expected_sweep, SWEEP_TOLERANCE)
    items.append(StatItem(group, "탐색 한 바퀴", _summary(sweeps, " ms"),
                          f"{expected_sweep:.0f} ms" if expected_sweep else "",
                          level, sweeps, "ms", note=f"기준 = 격자 {cells}칸 × {BEAM_SLOT_MS} ms ÷ 탐색 점유율"))

    delays = _confirmation_delays(records)
    level = IDLE
    if delays:
        level = _grade(max(abs(value - parameters.confirmation_delay_ms) for value in delays),
                       CONFIRMATION_TOLERANCE_MS)
    items.append(StatItem(group, "탐색→확인 지연", _summary(delays, " ms"), f"{parameters.confirmation_delay_ms} ms",
                          level, delays, "ms",
                          note="" if has_direction else "탐지 CSV에는 빔 방향이 없어 플롯 위치로 짝을 찾음"))

    periods: List[float] = []
    for segment in _by_segment(records).values():
        last_tracking: Dict[int, int] = {}
        for record in segment:
            if record.beam_type != BeamType.TRACKING:
                continue
            if record.beam_id in last_tracking:
                periods.append(float(record.time_ms - last_tracking[record.beam_id]))
            last_tracking[record.beam_id] = record.time_ms
    level = IDLE
    if periods:
        level = _grade(max(abs(value - parameters.tracking_interval_ms) for value in periods), PERIOD_TOLERANCE_MS)
    items.append(StatItem(group, "추적 주기", _summary(periods, " ms"), f"{parameters.tracking_interval_ms} ms",
                          level, periods, "ms"))

    group = "추적"
    tracks = _track_stats(records, end, parameters.tracking_interval_ms,
                          parameters.max_consecutive_tracking_misses)
    attempts = [track for track in tracks if track.has_confirmation]
    confirmed = sum(track.confirmed for track in attempts)
    items.append(StatItem(group, "확인 → 트랙", f"확인 빔 {len(attempts)}건 중 {confirmed}건 추적 시작"
                          + (f" ({confirmed / len(attempts) * 100:.0f}%)" if attempts else ""),
                          note="확인 빔 뒤에 같은 ID로 추적 빔이 나간 경우를 성공으로 셈. "
                               "녹화 전에 시작한 트랙은 빠짐"))
    items.append(StatItem(group, "트랙 수", f"{sum(track.confirmed for track in tracks)}개 "
                          f"(종료 {sum(track.confirmed and track.ended for track in tracks)}개)"))
    tracking_records = [record for record in records if record.beam_type == BeamType.TRACKING]
    if has_direction:
        misses = sum(record.outcome == Outcome.MISSED for record in tracking_records)
        judged = sum(record.outcome in (Outcome.MISSED, Outcome.UPDATED) for record in tracking_records)
        miss_note = "트랙 목록에서 갱신되지 않은 추적 빔"
    else:
        misses = sum(record.plots == [] for record in tracking_records)
        judged = len(tracking_records)
        miss_note = "플롯이 0개인 추적 빔"
    rate = misses / judged if judged else 0.0
    items.append(StatItem(group, "추적 빔 미탐", f"{misses}/{judged} ({rate * 100:.1f}%)" if judged else "-",
                          "", _grade(rate, (0.05, 0.2)) if judged else IDLE, note=miss_note))
    lifetimes = [track.lifetime_s for track in tracks if track.confirmed]
    items.append(StatItem(group, "트랙 수명", _summary(lifetimes, " s", 1), "", IDLE, lifetimes, "s"))
    errors = [record.angle_error_m for record in records
              if record.angle_error_m is not None and record.beam_type == BeamType.TRACKING]
    if has_direction:
        gate = math.sqrt(parameters.position_nds_threshold) * parameters.measurement_standard_deviation_m
        rms = _rms(errors)
        items.append(StatItem(group, "빔 축-탐지 차이 (추적 빔)",
                              "-" if rms is None else f"RMS {rms:.1f} m (최대 {max(errors):.1f} m)",
                              f"게이트 하한 약 {gate:.0f} m",
                              IDLE if rms is None else _grade(rms / gate, (0.8, 1.5)), errors, "m",
                              note="예측 방향과 연관 탐지 사이 수직 거리(모니터 추정)"))
    multi = sum(1 for record in records if record.plots and len(record.plots) >= 2)
    items.append(StatItem(group, "플롯 2개 이상인 빔", f"{multi}개", note="한 빔에 표적이 둘 이상 들어온 경우"))

    group = "통신"
    if has_direction:
        unanswered = sum(record.plots is None for record in records)
        items.append(StatItem(group, "탐지 결과를 못 받은 빔", f"{unanswered}개", "0개",
                              _grade(unanswered, (0, max(5, total // 1000))),
                              note="모의기 무응답이거나 모니터 큐에서 버려진 경우"))
    if extra:
        items.append(StatItem(group, "처리부 연결 끊김", f"{extra.get('disconnects', 0)}회", "0회",
                              WARN if extra.get("disconnects") else OK))
    if message_log is not None:
        from aesa_monitor.io.processor_log import (
            CATEGORY_ATTITUDE, CATEGORY_PROBLEM, CATEGORY_REJECTED,
        )
        counted = message_log.counts()
        statuses: Dict[str, int] = {}
        for event in message_log.events:
            if event.category == CATEGORY_REJECTED:
                status = event.details.get("status", "?")
                statuses[status] = statuses.get(status, 0) + 1
        group = "처리부 로그"
        items.append(StatItem(group, "요청 빔 거부", f"{counted[CATEGORY_REJECTED]}건"
                              + (" (" + ", ".join(f"{key} {value}" for key, value in statuses.items()) + ")"
                                 if statuses else ""), "", WARN if counted[CATEGORY_REJECTED] else OK))
        ignored = sum(1 for event in message_log.events
                      if event.category == CATEGORY_ATTITUDE and event.details.get("action") != "applied")
        items.append(StatItem(group, "자세정보 무시", f"{ignored}건", "", WARN if ignored else OK,
                              note="운용 OFF 중 수신 또는 이미 설정됨"))
        items.append(StatItem(group, "경고·오류", f"{counted[CATEGORY_PROBLEM]}건", "",
                              BAD if counted[CATEGORY_PROBLEM] else OK))
    return StatsReport(source, len(records), (start, end), items, tracks)
