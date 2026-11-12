"""탭마다 맨 위에 보여주는 판정 한 줄과 지표별 판정.

판정 기준은 처리부 설정(.env의 TARGET_*)과 ICD 값(빔 10 ms, 격자 칸 수)에서 나온다.
경계값은 모니터에서 정한 값이며 처리부의 요구사항이 아니다.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.events import PEER_CONSOLE, PEER_NAMES, PEER_SIMULATOR, SOURCE_CAPTURE
from aesa_monitor.core.protocol import BeamType, MessageId
from aesa_monitor.core.state import BEAM_SLOT_MS, MonitorState
from aesa_monitor.core.tracking import Outcome

OK = "ok"
WARN = "warn"
BAD = "bad"
IDLE = "idle"
_RANK = {IDLE: 0, OK: 1, WARN: 2, BAD: 3}

WINDOW_MS = 5000
SWEEP_TOLERANCE = (0.10, 0.30)          # 기대값 대비 비율 (주의, 이상)
PERIOD_TOLERANCE_MS = (20, 100)
CONFIRMATION_TOLERANCE_MS = (20, 100)
MISSED_SLOT_TOLERANCE = (0, 5)
RECENT_END_MS = 5000


@dataclass
class Check:
    """지표 하나의 값과 판정."""

    level: str
    value: Optional[float]
    expected: Optional[float] = None
    note: str = ""


@dataclass
class Verdict:
    level: str
    text: str
    problems: List[str] = field(default_factory=list)


def worst(levels) -> str:
    result = IDLE
    for level in levels:
        if _RANK[level] > _RANK[result]:
            result = level
    return result


def _grade(difference: float, limits: Tuple[float, float]) -> str:
    if difference <= limits[0]:
        return OK
    if difference <= limits[1]:
        return WARN
    return BAD


# 빔

def sweep_check(state: MonitorState) -> Check:
    actual = state.search_sweep_ms()
    expected = state.expected_sweep_ms(WINDOW_MS)
    if actual is None or expected is None:
        return Check(IDLE, actual, expected)
    return Check(_grade(abs(actual - expected) / expected, SWEEP_TOLERANCE), actual, expected)


def period_check(state: MonitorState, parameters: TrackingParameters) -> Check:
    deviation = state.max_period_deviation(parameters.tracking_interval_ms)
    if deviation is None:
        return Check(IDLE, None, 0)
    return Check(_grade(deviation, PERIOD_TOLERANCE_MS), deviation, 0)


def confirmation_check(state: MonitorState, parameters: TrackingParameters) -> Check:
    delay = state.latest_confirmation_delay()
    expected = parameters.confirmation_delay_ms
    if delay is None:
        return Check(IDLE, None, expected)
    return Check(_grade(abs(delay - expected), CONFIRMATION_TOLERANCE_MS), delay, expected)


def gap_check(state: MonitorState) -> Check:
    if state.now_ms() is None:
        return Check(IDLE, None, 0)
    missed = state.missed_beam_slots(WINDOW_MS)
    return Check(_grade(missed, MISSED_SLOT_TOLERANCE), missed, 0)


def beam_verdict(state: MonitorState, parameters: TrackingParameters) -> Verdict:
    if not state.connected:
        return Verdict(IDLE, "처리부에 연결되지 않았습니다")
    if state.paused():
        if state.fresh_status() is None:
            return Verdict(IDLE, "운용 OFF 상태입니다 (캡처한 운용 명령 0xCA01 기준)")
        return Verdict(IDLE, "처리부가 정지 상태입니다")
    if not state.recent_beams(1000):
        return Verdict(WARN, "빔이 나가지 않습니다 — 운용 OFF이거나 자세정보를 받기 전입니다",
                       ["최근 1초 빔 없음"])

    sweep = sweep_check(state)
    period = period_check(state, parameters)
    confirmation = confirmation_check(state, parameters)
    gaps = gap_check(state)
    problems = []
    if sweep.level in (WARN, BAD):
        problems.append(f"탐색 프레임 시간 {sweep.value / 1000:.2f} s (기대 {sweep.expected / 1000:.2f} s)")
    if period.level in (WARN, BAD):
        problems.append(f"추적 주기 오차 최대 {period.value:.0f} ms")
    if confirmation.level in (WARN, BAD):
        problems.append(f"탐색→확인 {confirmation.value:.0f} ms (설정 {confirmation.expected:.0f} ms)")
    if gaps.level in (WARN, BAD):
        problems.append(f"최근 5초 빠진 빔 {gaps.value:.0f}개")
    level = worst([OK, sweep.level, period.level, confirmation.level, gaps.level])
    if problems:
        return Verdict(level, " · ".join(problems), problems)

    parts = []
    if sweep.value is not None:
        parts.append(f"탐색 프레임 시간 {sweep.value / 1000:.2f} s")
    if period.value is not None:
        parts.append(f"추적 주기 오차 {period.value:.0f} ms")
    parts.append("빠진 빔 없음")
    return Verdict(OK, "정상 — " + ", ".join(parts))


# 추적

def track_verdict(state: MonitorState, parameters: TrackingParameters) -> Verdict:
    history = state.tracking
    track_ids = history.track_ids()
    active = sorted(history.latest_tracks)
    if not track_ids and not active:
        return Verdict(IDLE, "트랙 없음 — 탐색에서 표적을 찾으면 확인 빔 뒤에 트랙이 생깁니다")

    now = state.now_ms()
    limit = parameters.max_consecutive_tracking_misses
    problems = []
    level = OK
    for track_id in active:
        summary = history.summary(track_id)
        if summary.consecutive_misses >= max(limit - 1, 1):
            problems.append(f"#{track_id} 연속 미탐 {summary.consecutive_misses}/{limit}")
            level = worst([level, BAD])
        elif summary.consecutive_misses > 0:
            problems.append(f"#{track_id} 미탐 {summary.consecutive_misses}회")
            level = worst([level, WARN])
    ended = []
    for track_id in track_ids:
        cycles = history.cycles(track_id)
        if (cycles and cycles[-1].outcome == Outcome.ENDED and now is not None and
                now - cycles[-1].time_ms <= RECENT_END_MS):
            ended.append(f"#{track_id}")
    if ended:
        problems.append(f"최근 종료 {', '.join(ended)}")
        level = worst([level, WARN])
    head = f"트랙 {len(active)}개 추적 중"
    if problems:
        return Verdict(level, head + " · " + " · ".join(problems), problems)
    return Verdict(OK, f"정상 — {head}, 최근 미탐 없음")


# 통신

def link_rates(state: MonitorState) -> Tuple[float, float]:
    traffic = state.traffic
    return traffic.rate(MessageId.BEAM_TRANSMIT), traffic.rate(MessageId.PLOT_INFORMATION)


def expected_beam_rate(state: MonitorState) -> float:
    """처리부 시각 1초에 빔 100개. 처리부 속도 배율만큼 모니터 쪽 수신 속도가 바뀐다."""
    return 1000.0 / BEAM_SLOT_MS * state.time_scale()


def comm_verdict(state: MonitorState) -> Verdict:
    capture = state.source == SOURCE_CAPTURE
    if not state.connected:
        detail = f" ({state.connection_detail})" if state.connection_detail else ""
        if capture:
            return Verdict(BAD, f"캡처가 멈췄습니다{detail}")
        return Verdict(BAD, f"처리부 연결 끊김{detail} — 1초마다 다시 접속합니다")
    if state.fresh_status() is None and not capture:
        return Verdict(WARN, "처리부 상태 메시지(0xAD01)가 오지 않습니다")
    problems = []
    level = OK
    for peer in (PEER_CONSOLE, PEER_SIMULATOR):
        connected = state.peer_connected(peer)
        if connected is None:
            problems.append(f"{PEER_NAMES[peer]} 연결 상태 모름 (아직 패킷 없음)")
            level = worst([level, WARN])
        elif not connected:
            problems.append(f"{PEER_NAMES[peer]} 연결 안 됨")
            level = BAD
    beams, detections = link_rates(state)
    if not state.paused() and beams > 0:
        if detections < beams * 0.9:
            problems.append(f"모의기 응답이 빔보다 적음 ({detections:.0f}/s < {beams:.0f}/s)")
            level = worst([level, WARN])
        expected = expected_beam_rate(state)
        if beams < expected * 0.8:
            problems.append(f"빔 수신 {beams:.0f}/s (기대 {expected:.0f}/s)")
            level = worst([level, WARN])
    if problems:
        return Verdict(level, " · ".join(problems), problems)
    return Verdict(OK, f"정상 — 운용통제기·모의기 연결, 빔 {beams:.0f}/s, 탐지 {detections:.0f}/s")


def beam_type_share_text(state: MonitorState) -> str:
    occupancy = state.occupancy(WINDOW_MS)
    names = {BeamType.SEARCH: "탐색", BeamType.CONFIRMATION: "확인", BeamType.TRACKING: "추적"}
    return " / ".join(f"{names[beam_type]} {occupancy[beam_type] * 100:.0f}%" for beam_type in BeamType)
