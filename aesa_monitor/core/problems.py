"""문제 자동 기록: 모니터가 판정한 문제 상황을 사건 단위로 모은다.

사람이 북마크를 누르지 않아도, 테스트나 실제 운용 중 문제가 생긴 위치를 나중에 바로 찾아갈 수 있게 한다.
같은 감지 로직을 두 곳에서 쓴다.
- 실시간: 화면 갱신 때 ProblemDetector.update(state)를 불러 상단 '문제 n건'에 보여준다.
- 분석: 녹화를 열 때 scan_events()로 처음부터 다시 계산한다. 기준을 고치면 예전 녹화에도 그대로 적용된다.

기준은 새로 만들지 않고 실시간 판정(core/verdict.py)과 같은 값을 쓴다. 모니터 추정이 섞인 값
(예측-측정 잔차, 연관 탐지 선택과 그에 기댄 '빔 안 위치')은 넣지 않고 사실에 가까운 것만 기록한다.

상태가 이어지는 문제는 update()가 일정 간격(0.25 s)으로 상태를 보고 판단한다. 연결 변화(ConnectionChanged,
PeerLinkChanged)는 그 간격보다 짧게 끊겼다 이어질 수 있으므로 observe()로 사건마다 받는다.

사건 묶기: 같은 종류(트랙 문제는 같은 트랙)가 MERGE_MS 안에 다시 생기면 한 사건으로 합친다.
상태가 이어지는 문제(예: 탐색 프레임 시간 이탈)는 시작부터 풀릴 때까지를 한 구간으로 남긴다.

위치: 처리부 시각(ms)과 state clock(녹화에서는 녹화 시작 후 초)을 함께 남긴다. 처리부가 다시 시작하면
처리부 시각이 0부터 다시 세므로, 녹화에서 위치를 찾을 때는 clock 값(start_s)을 쓴다.
사건 시각이 지금보다 앞선 경우(추적 결과는 늦게 확정됨)에는 최근 (처리부 시각, clock) 표본으로 clock을 보간한다.
"""

from __future__ import annotations

from bisect import insort
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Deque, Dict, Iterable, List, Optional, Set, Tuple

from aesa_monitor.core.config import TrackingParameters
from aesa_monitor.core.events import (
    PEER_CONSOLE, PEER_NAMES, PEER_SIMULATOR, ConnectionChanged, Event, PeerLinkChanged,
)
from aesa_monitor.core.protocol import BeamType
from aesa_monitor.core.state import BEAM_SLOT_MS, TIME_RESET_THRESHOLD_MS, MonitorState
from aesa_monitor.core.tracking import Outcome
from aesa_monitor.core.verdict import (
    BAD, CONFIRMATION_TOLERANCE_MS, MISSED_SLOT_TOLERANCE, WARN, period_check, sweep_check, worst,
)

# 같은 종류의 문제가 이 시간(처리부 시각) 안에 다시 생기면 한 사건으로 합친다.
MERGE_MS = 2000
# 처리부 시각을 모를 때(연결 끊김 등) 쓰는 clock 기준 합치기 시간(초)
MERGE_S = 2.0
# 추적 사이클 결과는 트랙 목록이 늦게 와서 바뀔 수 있으므로 이만큼(추적 주기의 배수) 지난 사이클만 본다.
SETTLE_INTERVALS = 2
# 녹화를 다시 계산할 때 판정을 갱신하는 간격(녹화 시간, 초)
SCAN_STEP_S = 0.25
# 운용 중인데 이 시간 동안 빔이 없으면 '빔 없음'
NO_BEAM_MS = 1000
# clock 보간용 표본 수(갱신마다 하나)
CLOCK_SAMPLES = 400
# 미탐 한도로 사라진 트랙이 이 시간(처리부 시각) 안에 이 수 이상이면 하나의 '동시 소실'로 묶는다.
# 시나리오 한 주기가 끝나 모의기가 표적을 없애면 모든 트랙이 함께 미탐 한도로 지워진다(2026-11-19 녹화: 10개가 1초 안에).
MASS_LOSS_WINDOW_MS = 3000
MASS_LOSS_MIN_TRACKS = 3
MASS_LOSS_KEEP_MS = 3 * MASS_LOSS_WINDOW_MS   # 늦게 온 소실도 묶을 수 있게 최근 소실을 두는 시간

CATEGORY_BEAM = "빔"
CATEGORY_TRACK = "추적"
CATEGORY_LINK = "통신"
CATEGORY_LOG = "처리부 로그"
TRACK_KINDS = ("miss", "ended", "confirm-fail", "mass-loss")
LINK_EVENTS = (ConnectionChanged, PeerLinkChanged)


@dataclass
class Problem:
    """문제 사건 하나."""

    kind: str                        # 종류 키(예: "gap", "miss")
    category: str                    # 빔·추적·통신·처리부 로그
    level: str                       # "warn" 또는 "bad"
    title: str
    detail: str = ""
    start_ms: Optional[int] = None   # 처리부 시각
    end_ms: Optional[int] = None
    start_s: Optional[float] = None  # state clock. 녹화에서는 녹화 시작 후 초
    end_s: Optional[float] = None
    track_id: Optional[int] = None
    count: int = 1                   # 합쳐진 발생 횟수
    amount: int = 0                  # 종류별 누적 값(빠진 빔 칸 수 등)
    segment: Optional[int] = None    # 녹화의 처리부 실행 구간 번호(녹화를 다시 계산할 때만)
    open: bool = True                # 아직 이어지는 중인지(실시간)

    @property
    def duration_ms(self) -> Optional[int]:
        if self.start_ms is None or self.end_ms is None:
            return None
        return self.end_ms - self.start_ms

    @property
    def is_range(self) -> bool:
        """0.5초 넘게 이어진 사건이면 구간으로 그린다."""
        duration = self.duration_ms
        return duration is not None and duration >= 500


@dataclass
class _Open:
    problem: Problem
    last_ms: Optional[int]
    last_s: float
    grace_ms: int


@dataclass
class ProblemDetector:
    """MonitorState를 주기적으로 보고 문제 사건을 모은다."""

    parameters: TrackingParameters
    problems: List[Problem] = field(default_factory=list)
    _open: Dict[Tuple[str, Optional[int]], _Open] = field(default_factory=dict)
    _seen_cycles: Set[Tuple[int, int, int]] = field(default_factory=set)
    _seen_gap: Optional[Tuple[int, int]] = None
    _seen_delay: Optional[Tuple[int, int]] = None
    _last_ms: Optional[int] = None
    _connects: int = 0
    _was_connected: bool = False
    _epoch: int = 0
    _samples: Deque[Tuple[int, float]] = field(default_factory=lambda: deque(maxlen=CLOCK_SAMPLES))
    _live_tracks: Set[int] = field(default_factory=set)
    # 최근 미탐 한도로 사라진 트랙: (마지막 사이클 시각, 트랙, 끝 미탐 구간 시작 시각). 시각 순으로 둔다.
    _recent_losses: List[Tuple[int, int, int]] = field(default_factory=list)
    # 정상 종료(영역 이탈)나 동시 소실로 처리한 트랙: {트랙: 끝 미탐 구간 시작 시각}. 그 시각부터의 사이클만
    # 기록하지 않는다(늦게 확정되는 끝 미탐). 그 앞의 미탐은 트랙 생애 중의 문제라 남긴다.
    _quiet_tracks: Dict[int, int] = field(default_factory=dict)
    # 최근 소실 트랙의 사이클(동시 소실로 묶을 때 끝 미탐 구간을 문제에서 덜어 내는 데 쓴다)
    _loss_cycles: Dict[int, list] = field(default_factory=dict)
    _mass_loss: Optional[Problem] = None
    _mass_loss_ids: Set[int] = field(default_factory=set)
    _mass_loss_span: Tuple[int, int] = (0, 0)   # 지금 사건에 든 소실의 처음·마지막 처리부 시각

    # 바깥에서 쓰는 것

    def update(self, state: MonitorState) -> None:
        """지금 상태를 보고 문제를 열거나 늘리거나 닫는다."""
        now_s = state.clock()
        self._watch_restart(state, now_s)
        now_ms = state.now_ms()
        if now_ms is not None and state.connected:
            if not self._samples or now_ms >= self._samples[-1][0]:
                self._samples.append((now_ms, now_s))
        self._watch_link(state, now_s, now_ms)
        if state.connected and now_ms is not None:
            self._watch_beams(state, now_s, now_ms)
            self._watch_tracks(state, now_s, now_ms)
        self._close_stale(now_s, now_ms)

    def reset(self) -> None:
        """화면 초기화: 문제 목록을 새로 시작한다.

        연결 추적(끊김 감지에 쓰는 _was_connected·연결 횟수·처리부 시각)은 이어 간다. 새 감지기로 바꾸면 연결된 채로
        초기화한 뒤의 끊김을 놓친다(observe는 연결된 적이 있을 때만 끊김을 연다). 지금 이어지는 연결 문제(처리부 끊김,
        상대 끊김)도 지금 상태라서 새 목록에 남긴다.
        """
        ongoing = {key: item for key, item in self._open.items()
                   if key[0] == "disconnect" or key[0].startswith("peer-")}
        self._open = ongoing
        self.problems = [item.problem for item in ongoing.values()]
        self._reset_marks()

    def observe(self, event: Event, state: MonitorState) -> None:
        """연결 변화를 사건마다 받는다. state.apply(event) 직후에 부른다.

        update()는 일정 간격으로 상태만 보므로, 그 사이에 끊겼다 다시 이어지면 끊김을 놓친다(재연결하면 처리부 기록이
        지워지므로 짧아도 실제 끊김이다). 그래서 끊김·재연결 시각을 사건 그대로 남긴다.
        """
        now_s = state.clock()
        if isinstance(event, ConnectionChanged):
            if not event.connected:
                if self._was_connected:
                    # 끊겨도 state는 기록을 지우지 않으므로(다시 연결될 때 지움) now_ms는 끊기기 직전 처리부 시각이다.
                    self._report("disconnect", CATEGORY_LINK, BAD, "처리부 연결 끊김", event.detail or "",
                                 state.now_ms(), now_s, occurrence=False)
            else:
                self._close_disconnect(now_s)
            self._was_connected = event.connected
        elif isinstance(event, PeerLinkChanged):
            key = (f"peer-{event.peer}", None)
            name = PEER_NAMES.get(event.peer, event.peer)
            if not event.connected:
                self._report(key[0], CATEGORY_LINK, BAD, f"{name} 연결 끊김", event.detail or "통신 캡처 기준",
                             state.now_ms(), now_s)
            else:
                item = self._open.pop(key, None)
                if item is not None:
                    item.problem.end_s = now_s
                    item.problem.end_ms = state.now_ms() or item.problem.end_ms
                    item.problem.open = False

    def _close_disconnect(self, now_s: float) -> None:
        item = self._open.pop(("disconnect", None), None)
        if item is not None:
            item.problem.end_s = now_s
            item.problem.detail = (f"{item.problem.detail} · " if item.problem.detail else "") + (
                f"{now_s - item.problem.start_s:.1f} s 뒤 다시 연결")
            item.problem.open = False

    def finish(self) -> None:
        """기록이 끝났을 때(녹화 끝) 이어지는 사건을 모두 닫는다."""
        for item in self._open.values():
            item.problem.open = False
        self._open.clear()

    def worst_open(self) -> Optional[str]:
        levels = [item.problem.level for item in self._open.values()]
        return worst(levels) if levels else None

    # 사건 열기·늘리기·닫기

    def _clock_for(self, at_ms: int, now_s: float) -> float:
        """처리부 시각 at_ms일 때의 clock을 최근 표본으로 보간한다. 표본 밖이면 가장 가까운 표본을 쓴다."""
        samples = self._samples
        if not samples:
            return now_s
        if at_ms <= samples[0][0]:
            return samples[0][1]
        previous = samples[0]
        for sample in samples:
            if sample[0] >= at_ms:
                span = sample[0] - previous[0]
                if span <= 0:
                    return sample[1]
                return previous[1] + (sample[1] - previous[1]) * (at_ms - previous[0]) / span
            previous = sample
        return samples[-1][1]

    @staticmethod
    def _trailing_run_start(cycles) -> Optional[int]:
        """끝에 이어진 미탐·종료 사이클 구간의 시작 시각."""
        start = None
        for cycle in reversed(cycles):
            if cycle.outcome not in (Outcome.MISSED, Outcome.ENDED):
                break
            start = cycle.time_ms
        return start

    def _group_loss(self, track_id: int, cycles, last_ms: int, now_s: float, grace: int) -> bool:
        """미탐 한도로 사라진 트랙을 최근 소실과 묶는다. 묶였으면(동시 소실로 기록) True.

        MASS_LOSS_WINDOW_MS 안에 MASS_LOSS_MIN_TRACKS개 이상이면 트랙마다의 '미탐'·'종료' 대신
        '트랙 N개 동시 소실' 하나로 남긴다. 사건 하나로는 남기므로 모의기 정지 같은 이상도 사라지지 않는다.
        한 사건에 든 소실은 처음과 마지막이 MASS_LOSS_WINDOW_MS 안이다. 벗어나는 소실은 새 묶음으로 따로 센다
        (열린 사건의 유예 시간 안이라도 합치지 않는다: 4초 간격의 두 묶음이 '6개 동시 소실'이 되지 않게).
        소실이 처리부 시각 순서대로 오지 않아도(빠른 재생에서 한 번에 여러 트랙이 사라짐, 처리부가 트랙을 지우는
        때가 원인마다 다름) 묶음은 처리부 시각으로 정한다. 최근 소실은 시각 순으로 조금 넉넉히(MASS_LOSS_KEEP_MS) 두고,
        이 소실을 포함하는 창 가운데 소실이 가장 많은 창을 고른다.
        """
        run_start = self._trailing_run_start(cycles)
        loss = (last_ms, track_id, run_start if run_start is not None else last_ms)
        insort(self._recent_losses, loss)
        self._loss_cycles[track_id] = list(cycles)
        newest_ms = self._recent_losses[-1][0]
        self._recent_losses = [item for item in self._recent_losses if item[0] >= newest_ms - MASS_LOSS_KEEP_MS]
        kept = {item[1] for item in self._recent_losses}
        self._loss_cycles = {key: value for key, value in self._loss_cycles.items() if key in kept}
        if loss not in self._recent_losses:
            return False  # 이미 지난 소실들보다 한참 앞선 소실: 따로 기록한다
        group = self._mass_loss
        item = self._open.get(("mass-loss", None))
        if item is None or item.problem is not group:
            group = None  # 유예 시간이 지나 이미 닫혔다
        low, high = self._mass_loss_span
        if group is not None and max(high, last_ms) - min(low, last_ms) > MASS_LOSS_WINDOW_MS:
            if last_ms < low:
                return False  # 지금 사건보다 앞선 소실이 늦게 왔다: 사건은 그대로 두고 따로 기록한다
            group = None
        if group is None:
            # 새 묶음은 언제나 새 사건이다. 열린 동시 소실 사건이 남아 있어도 _report가 그것에 붙이지 않게 닫는다.
            self._close_mass_loss()
        # 이미 어느 사건에 들어간 소실은 새 묶음의 개수에 넣지 않는다.
        fresh = [item for item in self._recent_losses if item[1] not in self._quiet_tracks]
        if group is None:
            fresh = self._densest_window(fresh, last_ms)
            if len(fresh) < MASS_LOSS_MIN_TRACKS:
                return False
            low = high = fresh[0][0]
        for at_ms, lost_id, since_ms in fresh:
            if max(high, at_ms) - min(low, at_ms) > MASS_LOSS_WINDOW_MS:
                continue  # 이 사건의 창 밖(지금 사건보다 앞선 소실): 따로 둔다
            low, high = min(low, at_ms), max(high, at_ms)
            self._silence(lost_id, since_ms, self._loss_cycles.get(lost_id, []))
            self._retract("ended", lost_id, since_ms)
            at_s = self._clock_for(at_ms, now_s)
            problem = self._report("mass-loss", CATEGORY_TRACK, WARN, "", "", at_ms, at_s, amount=1,
                                   grace_ms=grace + MASS_LOSS_WINDOW_MS)
            if problem is not self._mass_loss:
                self._mass_loss, self._mass_loss_ids = problem, set()
            self._mass_loss_ids.add(lost_id)
            if problem.start_ms is None or at_ms < problem.start_ms:
                problem.start_ms, problem.start_s = at_ms, min(problem.start_s, at_s)
            problem.title = f"트랙 {problem.amount}개 동시 소실"
            problem.detail = (f"{MASS_LOSS_WINDOW_MS / 1000:g}초 안에 미탐 한도로 함께 사라짐 — 시나리오 한 주기 종료"
                              f"(모의기 표적 제거) 또는 모의기 정지 추정 "
                              f"(트랙 {', '.join(f'#{item}' for item in sorted(self._mass_loss_ids))})")
        self._mass_loss_span = (low, high)
        return track_id in self._quiet_tracks

    @staticmethod
    def _densest_window(losses: List[Tuple[int, int, int]], at_ms: int) -> List[Tuple[int, int, int]]:
        """시각 순 소실 가운데, at_ms를 포함하는 MASS_LOSS_WINDOW_MS 창에서 소실이 가장 많은 창의 소실(같으면 이른 창)."""
        best: List[Tuple[int, int, int]] = []
        for start, _track, _since in losses:
            if not at_ms - MASS_LOSS_WINDOW_MS <= start <= at_ms:
                continue
            inside = [item for item in losses if start <= item[0] <= start + MASS_LOSS_WINDOW_MS]
            if len(inside) > len(best):
                best = inside
        return best

    def _close_mass_loss(self) -> None:
        """열린 동시 소실 사건을 닫는다. 다음 소실은 새 사건으로 연다."""
        item = self._open.pop(("mass-loss", None), None)
        if item is not None:
            item.problem.open = False
        self._mass_loss = None
        self._mass_loss_span = (0, 0)
        self._mass_loss_ids = set()

    def _silence(self, track_id: int, since_ms: int, cycles) -> None:
        """이 트랙의 since_ms(처리부 시각)부터의 사이클은 정상(영역 이탈, 동시 소실)이다. 그 뒤로 시작한 미탐 문제는
        지우고, 그 앞에서 시작해 이어진 미탐 문제는 since_ms 앞의 미탐만 남게 줄인다. 앞으로 확정되는 since_ms
        이후 사이클도 기록하지 않는다.
        """
        self._quiet_tracks[track_id] = min(since_ms, self._quiet_tracks.get(track_id, since_ms))
        self._retract("miss", track_id, since_ms)
        limit = self.parameters.max_consecutive_tracking_misses
        for problem in self.problems:
            if (problem.kind != "miss" or problem.track_id != track_id or problem.start_ms is None
                    or problem.end_ms is None or not problem.start_ms < since_ms <= problem.end_ms):
                continue
            runs = []   # since_ms 앞의, 이 문제에 든 미탐 사이클마다 그때까지의 연속 미탐 수
            consecutive = 0
            for cycle in cycles:
                if cycle.time_ms >= since_ms:
                    break
                consecutive = consecutive + 1 if cycle.outcome == Outcome.MISSED else 0
                if cycle.outcome == Outcome.MISSED and cycle.time_ms >= problem.start_ms:
                    runs.append((cycle.time_ms, consecutive))
            if not runs:
                continue  # 사이클 기록이 없다(오래되어 잘림): 그대로 둔다
            worst_run = max(run for _time, run in runs)
            problem.count = len(runs)
            problem.amount = worst_run
            problem.end_ms = runs[-1][0]
            problem.end_s = self._clock_for(problem.end_ms, problem.end_s if problem.end_s is not None else 0.0)
            problem.level = BAD if worst_run >= max(limit - 1, 1) else WARN
            problem.detail = f"연속 미탐 최대 {worst_run}/{limit}"
            item = self._open.pop(("miss", track_id), None)   # 트랙이 끝났으므로 더 이어지지 않는다
            if item is not None:
                item.problem.open = False

    def _retract(self, kind: str, track_id: int, since_ms: int) -> None:
        """since_ms(처리부 시각) 이후에 시작한 이 트랙의 문제를 지운다. 나중에 정상으로 밝혀진 경우에 쓴다."""
        def gone(problem: Problem) -> bool:
            return (problem.kind == kind and problem.track_id == track_id
                    and problem.start_ms is not None and problem.start_ms >= since_ms)

        self.problems = [problem for problem in self.problems if not gone(problem)]
        item = self._open.get((kind, track_id))
        if item is not None and gone(item.problem):
            del self._open[(kind, track_id)]

    def _report(self, kind: str, category: str, level: str, title: str, detail: str, at_ms: Optional[int],
                at_s: float, track_id: Optional[int] = None, amount: int = 0, occurrence: bool = True,
                grace_ms: int = MERGE_MS) -> Problem:
        """문제가 at_ms(처리부 시각)/at_s(clock)에 있다. 같은 종류의 열린 사건이 있으면 늘리고 없으면 연다.

        occurrence: 한 번의 발생(빠진 빔, 미탐 등)이면 True라 횟수를 센다. 이어지는 상태 확인이면 False.
        """
        key = (kind, track_id)
        item = self._open.get(key)
        if item is not None:
            problem = item.problem
            problem.level = worst([problem.level, level])
            problem.title = title
            problem.detail = detail
            problem.amount += amount
            if occurrence:
                problem.count += 1
            if at_ms is not None and (problem.end_ms is None or at_ms > problem.end_ms):
                problem.end_ms = at_ms
            if problem.end_s is None or at_s > problem.end_s:
                problem.end_s = at_s
            # 사건의 마지막 시각은 되돌리지 않는다(앞선 시각의 발생이 늦게 와도 유예 시간은 가장 늦은 발생부터)
            if at_ms is not None and (item.last_ms is None or at_ms > item.last_ms):
                item.last_ms = at_ms
            item.last_s = max(item.last_s, at_s)
            return problem
        problem = Problem(kind, category, level, title, detail, at_ms, at_ms, at_s, at_s, track_id,
                          count=1, amount=amount)
        self.problems.append(problem)
        self._open[key] = _Open(problem, at_ms, at_s, grace_ms)
        return problem

    def _close_stale(self, now_s: float, now_ms: Optional[int]) -> None:
        for key in list(self._open):
            item = self._open[key]
            if key[0] == "disconnect":
                continue  # 다시 연결될 때 닫는다
            if now_ms is not None and item.last_ms is not None:
                stale = now_ms - item.last_ms > item.grace_ms
            else:
                stale = now_s - item.last_s > MERGE_S
            if stale:
                item.problem.open = False
                del self._open[key]

    # 통신

    def _watch_link(self, state: MonitorState, now_s: float, now_ms: Optional[int]) -> None:
        """상대 연결 상태(처리부 상태 메시지 0xAD01 기준). 처리부 연결 끊김은 observe()가 사건으로 받는다."""
        item = self._open.get(("disconnect", None))
        if item is not None:
            item.problem.end_s = now_s
        if state.connected:
            for peer in (PEER_CONSOLE, PEER_SIMULATOR):
                if state.fresh_status() is not None and state.peer_connected(peer) is False:
                    self._report(f"peer-{peer}", CATEGORY_LINK, BAD, f"{PEER_NAMES[peer]} 연결 안 됨",
                                 "처리부 상태 메시지 기준", now_ms, now_s, occurrence=False)
        if state.connect_count != self._connects:
            # 다시 연결되면 처리부 기록이 지워지므로 이미 본 사이클·빈 칸 표시도 비운다.
            self._connects = state.connect_count
            self._reset_marks()

    def _watch_restart(self, state: MonitorState, now_s: float) -> None:
        """처리부 시각이 1초 넘게 되돌아가면 처리부가 다시 시작한 것이다(state·통계·재생 구간과 같은 규칙).

        state.processor_restarts만 보면 다시 연결된 뒤의 재시작(연결이 끊기며 기록이 지워짐)을 놓치므로 직접 본다.
        """
        now_ms = state.now_ms()
        if now_ms is None:
            return
        previous, self._last_ms = self._last_ms, now_ms if self._last_ms is None else max(self._last_ms, now_ms)
        if previous is None or now_ms + TIME_RESET_THRESHOLD_MS >= previous:
            return
        self._last_ms = now_ms
        self._reset_marks()
        # 재시작 앞뒤는 처리부 시각이 이어지지 않으므로 열린 사건을 닫는다. 연결 끊김은 다시 연결될 때 닫는다.
        for key in [key for key in self._open if key[0] != "disconnect"]:
            self._open.pop(key).problem.open = False
        self._report("restart", CATEGORY_LINK, WARN, "처리부 재시작",
                     f"처리부 시각이 {previous / 1000:.1f} s에서 {now_ms / 1000:.1f} s로 되돌아감", now_ms, now_s)

    def _reset_marks(self) -> None:
        # 재시작·재연결로 처리부 기록이 지워진 것은 트랙 종료가 아니므로 트랙 목록도 처음부터 다시 본다.
        self._live_tracks = set()
        self._epoch += 1
        self._seen_cycles.clear()
        self._seen_gap = None
        self._seen_delay = None
        self._samples.clear()
        # 트랙 소실 분류 장부도 비운다. 재시작한 처리부는 트랙 번호를 다시 쓸 수 있어, 남겨 두면
        # 같은 번호의 새 트랙 미탐을 조용히 건너뛰고, 재시작 전 시각이 새 동시 소실 묶음에 섞인다.
        # 열린 동시 소실 사건도 닫는다. 남겨 두면 다시 연결된 뒤의 소실 묶음이 그 사건에 붙어 개수가 부풀어 오른다
        # (처리부가 재시작하지 않아 시각이 이어지는 재연결은 _watch_restart가 열린 사건을 닫지 않는다).
        self._quiet_tracks.clear()
        self._recent_losses.clear()
        self._loss_cycles.clear()
        self._close_mass_loss()

    # 빔

    def _watch_beams(self, state: MonitorState, now_s: float, now_ms: int) -> None:
        if state.paused() is False and not state.recent_beams(NO_BEAM_MS):
            self._report("no-beam", CATEGORY_BEAM, WARN, "빔이 나가지 않음", "운용 중인데 최근 1초 빔 없음",
                         now_ms, now_s, occurrence=False)

        # 빠진 빔: 새로 생긴 빈 칸만 센다. 사건 하나의 빠진 칸 합계가 1~5면 주의, 6 이상이면 이상(실시간 판정과 같음).
        for time_ms, missed in state.beam_gaps:
            if self._seen_gap is not None and (time_ms, missed) <= self._seen_gap:
                continue
            item = self._open.get(("gap", None))
            total = missed + (item.problem.amount if item is not None else 0)
            level = WARN if total <= MISSED_SLOT_TOLERANCE[1] else BAD
            # beam_gaps의 시각은 빈 칸 다음에 온 빔이다. 첫 빈 칸 시각으로 남긴다.
            first_missing_ms = time_ms - missed * BEAM_SLOT_MS
            self._report("gap", CATEGORY_BEAM, level, f"빠진 빔 {total}칸",
                         "처리부가 늦었거나 모니터 큐가 넘쳐 빔 복사본이 버려짐", first_missing_ms,
                         self._clock_for(first_missing_ms, now_s), amount=missed)
        if state.beam_gaps:
            self._seen_gap = state.beam_gaps[-1]

        sweep = sweep_check(state)
        if sweep.level in (WARN, BAD):
            self._report("sweep", CATEGORY_BEAM, sweep.level, f"탐색 프레임 시간 {sweep.value / 1000:.2f} s",
                         f"기대 {sweep.expected / 1000:.2f} s", now_ms, now_s, occurrence=False)

        period = period_check(state, self.parameters)
        if period.level in (WARN, BAD):
            self._report("period", CATEGORY_BEAM, period.level, f"추적 주기 오차 {period.value:.0f} ms",
                         f"설정 주기 {self.parameters.tracking_interval_ms} ms", now_ms, now_s, occurrence=False)

        # 탐색→확인 지연: 확인 빔마다 본다(마지막 값만 보면 다음 확인 빔까지 계속 이상으로 남는다).
        expected = self.parameters.confirmation_delay_ms
        for time_ms, delay in list(state.confirmation_delays):
            if self._seen_delay is not None and (time_ms, delay) <= self._seen_delay:
                continue
            difference = abs(delay - expected)
            if difference > CONFIRMATION_TOLERANCE_MS[0]:
                level = WARN if difference <= CONFIRMATION_TOLERANCE_MS[1] else BAD
                self._report("confirm-delay", CATEGORY_BEAM, level, f"탐색→확인 {delay} ms", f"설정 {expected} ms",
                             time_ms, self._clock_for(time_ms, now_s))
        if state.confirmation_delays:
            self._seen_delay = state.confirmation_delays[-1]

    # 추적

    def _watch_tracks(self, state: MonitorState, now_s: float, now_ms: int) -> None:
        history = state.tracking
        limit = self.parameters.max_consecutive_tracking_misses
        settle_ms = SETTLE_INTERVALS * self.parameters.tracking_interval_ms
        # 추적 결과는 settle_ms만큼 늦게 확정되므로 그만큼 더 기다려야 같은 트랙 미탐을 한 사건으로 묶는다.
        grace = MERGE_MS + settle_ms

        # 트랙 종료: 처리부 트랙 목록에서 사라진 트랙. 정상 종료는 문제로 남기지 않는다.
        #   - 운용 OFF: 처리부가 트랙을 모두 지운다(처리부 로그 'OFF, cleared tracks=N').
        #   - 영역 이탈: 다음 추적 빔이 나가지 않고(처리부가 요청을 OUT_OF_COVERAGE로 거부) 4초 뒤 지운다
        #     ('Removed unanswered track'). 미탐이 한도에 이르지 않았는데 다음 빔 없이 사라진 경우로 본다.
        #     2026-11-19 녹화에서 처리부 로그의 영역 밖 거부 트랙과 모두 일치(미탐 0~1회, 다음 빔 없음).
        #     다른 이유로 요청이 버려져도(예: 자세 정보 없음) 같은 모양이라 구분하지 못한다(처리부 로그로 확인).
        # 남기는 것: 미탐 한도로 지워진 트랙(연속 미탐 = 한도 - 1, 마지막 사이클이 한도째 미탐),
        #           다음 추적 빔은 나갔는데 결과 없이 지워진 트랙(모의기 응답 없음).
        current = set(history.latest_tracks)
        operation_off = state.paused() is True
        # 사라진 트랙은 마지막 사이클의 처리부 시각 순으로 본다(트랙 번호 순이 아니다). 빠른 재생에서는 한 번에
        # 몇 초 치 소실이 함께 보이므로, 시각 순이 아니면 동시 소실 묶음이 창보다 넓게 묶일 수 있다.
        def last_cycle_ms(track_id: int) -> int:
            cycles = history.cycles(track_id)
            return cycles[-1].time_ms if cycles else now_ms

        for track_id in sorted(self._live_tracks - current, key=lambda item: (last_cycle_ms(item), item)):
            if operation_off:
                continue
            cycles = history.cycles(track_id)
            misses = 0
            for cycle in reversed(cycles):
                if cycle.outcome == Outcome.ENDED:
                    continue
                if cycle.outcome != Outcome.MISSED:
                    break
                misses += 1
            next_beam_sent = bool(cycles) and cycles[-1].next_beam is not None
            if misses < limit - 1 and not next_beam_sent:
                # 영역 이탈(추정). 영역 경계에서 빗나간 마지막 빔들의 미탐도 이탈 과정이라 문제에서 뺀다
                # (트랙 생애 앞쪽의 미탐은 그대로 둔다).
                run_start = self._trailing_run_start(cycles)
                if run_start is not None:
                    self._silence(track_id, run_start, cycles)
                continue
            last_ms = cycles[-1].time_ms if cycles else now_ms
            if misses and self._group_loss(track_id, cycles, last_ms, now_s, grace):
                continue
            if misses:
                reason = f"직전 연속 미탐 {misses}회 (한도 {limit})"
            else:
                reason = "다음 추적 빔은 나갔는데 결과 없이 사라짐 — 모의기 응답 확인 (처리부 로그 'Removed unanswered track')"
            self._report("ended", CATEGORY_TRACK, WARN, f"트랙 #{track_id} 종료", reason, last_ms,
                         self._clock_for(last_ms, now_s), track_id=track_id, grace_ms=grace)
        self._live_tracks = current

        for track_id in history.track_ids():
            cycles = history.cycles(track_id)
            for position in range(max(0, len(cycles) - 16), len(cycles)):
                cycle = cycles[position]
                if cycle.time_ms > now_ms - settle_ms:
                    break
                mark = (self._epoch, track_id, cycle.time_ms)
                if mark in self._seen_cycles or cycle.outcome == Outcome.PENDING:
                    continue
                self._seen_cycles.add(mark)
                quiet_from = self._quiet_tracks.get(track_id)
                if quiet_from is not None and cycle.time_ms >= quiet_from:
                    continue
                at_s = self._clock_for(cycle.time_ms, now_s)
                if cycle.outcome == Outcome.MISSED:
                    consecutive = 0
                    index = position
                    while index >= 0 and cycles[index].outcome == Outcome.MISSED:
                        consecutive += 1
                        index -= 1
                    item = self._open.get(("miss", track_id))
                    worst_run = max(consecutive, item.problem.amount if item is not None else 0)
                    level = BAD if worst_run >= max(limit - 1, 1) else WARN
                    problem = self._report("miss", CATEGORY_TRACK, level, f"트랙 #{track_id} 미탐",
                                           f"연속 미탐 최대 {worst_run}/{limit}", cycle.time_ms, at_s,
                                           track_id=track_id, grace_ms=grace)
                    problem.amount = worst_run
                elif cycle.outcome == Outcome.ENDED and cycle.beam_type == BeamType.CONFIRMATION:
                    # 확인 빔 뒤 트랙이 만들어지지 않았다(트랙 목록에 나온 적 없음). 오경보 확인 실패일 수 있어 주의로 둔다.
                    self._report("confirm-fail", CATEGORY_TRACK, WARN, f"요청 #{track_id} 확인 실패",
                                 "확인 빔 뒤 트랙을 만들지 않음", cycle.time_ms, at_s, track_id=track_id,
                                 grace_ms=grace)
                # '빔 밖 탐지'(연관 탐지의 빔 안 위치 > 1)는 기록하지 않는다. 연관 탐지는 모니터가 m 거리로 고른 추정이라
                # 처리부(NDS)가 빔 안 탐지를 썼어도 빔 밖 탐지를 고를 수 있다. 빔 안 위치는 추적 탭에서 보는 값으로만 쓴다.
        if len(self._seen_cycles) > 20000:
            self._seen_cycles = {mark for mark in self._seen_cycles
                                 if mark[0] == self._epoch and mark[2] >= now_ms - 60000}


def scan_events(events: Iterable[Tuple[float, Event]], parameters: TrackingParameters,
                segment_at: Optional[Callable[[float], int]] = None,
                step_s: float = SCAN_STEP_S) -> List[Problem]:
    """녹화 이벤트(녹화 시각 초, 이벤트)를 처음부터 넣으며 문제를 모은다. 녹화를 열 때 쓴다.

    state clock이 녹화 시각이므로 Problem.start_s는 녹화 위치(초)다. segment_at을 주면 처리부 실행 구간 번호도 채운다.
    녹화 마지막의 연결 끊김(모니터를 끈 것)은 문제로 남기지 않는다(통계와 같은 규칙).
    """
    position = [0.0]
    state = MonitorState(clock=lambda: position[0])
    detector = ProblemDetector(parameters)
    next_update: Optional[float] = None
    last_data_s: Optional[float] = None
    for time_s, event in events:
        if next_update is None:
            next_update = time_s + step_s
        while time_s >= next_update:
            position[0] = next_update
            detector.update(state)
            next_update += step_s
        position[0] = time_s
        state.apply(event, time_s)
        if isinstance(event, LINK_EVENTS):
            detector.observe(event, state)
        if not isinstance(event, ConnectionChanged):
            last_data_s = time_s
    detector.update(state)
    detector.finish()
    problems = [problem for problem in detector.problems
                if not (problem.kind == "disconnect" and last_data_s is not None and problem.start_s is not None
                        and problem.start_s >= last_data_s)]
    if segment_at is not None:
        for problem in problems:
            if problem.start_s is not None:
                problem.segment = segment_at(problem.start_s)
    return problems


def level_counts(problems: Iterable[Problem]) -> Dict[str, int]:
    result = {WARN: 0, BAD: 0}
    for problem in problems:
        result[problem.level] = result.get(problem.level, 0) + 1
    return result
