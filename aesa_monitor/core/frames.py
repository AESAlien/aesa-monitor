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
from typing import Deque, Dict, List, Optional

from aesa_monitor.core.protocol import BeamType

BEAM_TYPES = (BeamType.SEARCH, BeamType.CONFIRMATION, BeamType.TRACKING)


@dataclass(frozen=True)
class FrameShare:
    """탐색 프레임 하나의 빔 종류별 개수."""

    index: int                 # 1부터 센 프레임 번호
    start_ms: int              # 프레임 첫 빔의 처리부 시각
    end_ms: int                # 다음 프레임 첫 탐색 빔의 처리부 시각(진행 중이면 마지막 빔 시각)
    counts: Dict[int, int]     # BeamType 값 → 빔 수
    complete: bool             # 탐색 빔 0번부터 격자 끝까지 다 돌고 닫힌 프레임인지

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms

    def share(self, beam_type: int) -> float:
        total = self.total
        return self.counts.get(int(beam_type), 0) / total if total else 0.0


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
        complete = (closed and self._first_search_id == 0 and self._last_search_id is not None
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
