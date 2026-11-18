"""탐색 프레임 단위 빔 점유율.

탐색 프레임은 탐색 격자를 처음(탐색 빔 0번)부터 끝까지 한 번 다 도는 구간이다.
처리부는 탐색 빔 번호를 격자 순서(0, 1, 2, ...)로 돌려 쓰므로, 탐색 빔 번호가 앞 탐색 빔보다
작아지면 새 프레임이 시작된 것으로 본다. 프레임 사이에 끼어든 확인·추적 빔은 그 프레임에 센다.

프레임마다 탐색·확인·추적 빔 수를 세면, 추적 트랙이 늘수록 탐색 비율이 줄고 추적 비율이 느는
흐름을 시간(프레임) 순서로 볼 수 있다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Dict, List, Optional, Tuple, Union

from aesa_monitor.core.protocol import BeamType

BEAM_TYPES = (BeamType.SEARCH, BeamType.CONFIRMATION, BeamType.TRACKING)
GAP = "gap"  # FrameShare.time_parts의 빈 칸(빔이 오지 않은 시간) 키


@dataclass(frozen=True)
class FrameShare:
    """탐색 프레임 하나의 빔 종류별 개수."""

    index: int                 # 1부터 센 프레임 번호
    start_ms: int              # 프레임 첫 빔의 처리부 시각
    end_ms: int                # 다음 프레임 첫 탐색 빔의 처리부 시각(진행 중이면 마지막 빔 시각)
    counts: Dict[int, int]     # BeamType 값 → 빔 수
    complete: bool             # 탐색 빔 0번부터 격자 끝까지 다 돌고 닫힌 프레임인지(앞 프레임 꼬리 빔이 섞이면 False)

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def share(self, beam_type: int) -> float:
        total = self.total
        return self.counts.get(int(beam_type), 0) / total if total else 0.0

    def count(self, beam_type: int) -> int:
        return self.counts.get(int(beam_type), 0)

    def time_parts(self, slot_ms: int) -> List[Tuple[Union[int, str], int, float]]:
        """프레임 시간을 탐색·확인·추적에 쓴 시간(빔 수 × slot_ms)과 빈 칸(GAP, 빔이 오지 않은 시간)으로 나눈다.

        (키, ms, 비율) 목록을 탐색·확인·추적·빈 칸 순서로 준다. 비율은 빔 수가 아니라 프레임 시간 기준이라, 처리부 지연이나
        모니터 큐 넘침으로 빈 칸이 생기면 탐색·확인·추적 비율의 합이 100%보다 작아지고 나머지가 빈 칸 비율이다(share()와 다름).
        프레임 시간이 빔 수 × slot_ms보다 짧으면(시각 반올림 등) 빈 칸은 0이고 빔 시간 합을 분모로 쓴다.
        """
        parts: List[Tuple[Union[int, str], int]] = [(int(beam_type), self.count(beam_type) * slot_ms)
                                                    for beam_type in BEAM_TYPES]
        used = sum(ms for _, ms in parts)
        parts.append((GAP, max(self.duration_ms - used, 0)))
        total = max(self.duration_ms, used)
        return [(key, ms, ms / total if total else 0.0) for key, ms in parts]

    @property
    def request_beams(self) -> int:
        """프레임 사이에 끼어든 확인·추적 빔 수."""
        return self.count(BeamType.CONFIRMATION) + self.count(BeamType.TRACKING)


def expected_tracking_beams(track_count: int, frame_duration_ms: float, interval_ms: float) -> Optional[float]:
    """탐색 프레임 하나 동안 나가야 할 추적 빔 수 = 트랙 수 × 프레임 시간 ÷ 추적 주기.

    트랙이 없거나 값이 0 이하면 None이다(그릴 기대값이 없음).
    """
    if track_count <= 0 or frame_duration_ms <= 0 or interval_ms <= 0:
        return None
    return track_count * frame_duration_ms / interval_ms


class FrameCounter:
    """빔을 처리부 시각 순서로 받아 탐색 프레임별 빔 수를 센다."""

    def __init__(self, max_frames: Optional[int] = None) -> None:
        self.frames: Deque[FrameShare] = deque(maxlen=max_frames)
        self._next_index = 1
        self._grid_size = 0
        self._reset_current()

    def _reset_current(self) -> None:
        self._start_ms: Optional[int] = None
        self._last_ms: Optional[int] = None
        self._counts: Dict[int, int] = {int(beam_type): 0 for beam_type in BEAM_TYPES}
        self._first_search_id: Optional[int] = None
        self._last_search_id: Optional[int] = None
        # 첫 탐색 빔보다 먼저 확인·추적 빔이 들어왔는지. 그 빔은 앞 프레임의 꼬리라 이 프레임은 앞이 잘린 것이다.
        self._lead_in = False

    def clear(self) -> None:
        self.frames.clear()
        self._next_index = 1
        self._grid_size = 0
        self._reset_current()

    def add(self, beam_type: int, beam_id: int, time_ms: int) -> None:
        if (beam_type == BeamType.SEARCH and self._last_search_id is not None
                and beam_id < self._last_search_id):
            self._close(time_ms, closed=True)
        if self._start_ms is None:
            self._start_ms = time_ms
        self._last_ms = time_ms
        if beam_type in self._counts:
            self._counts[beam_type] += 1
            if beam_type != BeamType.SEARCH and self._first_search_id is None:
                self._lead_in = True
        if beam_type == BeamType.SEARCH:
            if self._first_search_id is None:
                self._first_search_id = beam_id
            self._last_search_id = beam_id
            self._grid_size = max(self._grid_size, beam_id + 1)

    def break_frame(self) -> None:
        """처리부가 다시 시작하는 등 흐름이 끊겼을 때 진행 중인 프레임을 미완료로 닫는다."""
        if self._start_ms is not None:
            self._close(self._last_ms, closed=False)

    def _close(self, end_ms: int, closed: bool) -> None:
        complete = (closed and not self._lead_in and self._first_search_id == 0 and self._last_search_id is not None
                    and self._last_search_id + 1 >= self._grid_size)
        self.frames.append(FrameShare(self._next_index, self._start_ms, end_ms, dict(self._counts), complete))
        self._next_index += 1
        self._reset_current()

    def current(self) -> Optional[FrameShare]:
        """진행 중인 프레임. 아직 빔이 없으면 None이다."""
        if self._start_ms is None:
            return None
        return FrameShare(self._next_index, self._start_ms, self._last_ms, dict(self._counts), False)

    def complete_frames(self) -> List[FrameShare]:
        return [frame for frame in self.frames if frame.complete]
