"""녹화 재생. 녹화한 이벤트를 재생 위치까지 MonitorState에 넣는다.

화면은 실시간과 같은 MonitorState를 그대로 그리므로 탭 코드를 따로 두지 않는다.
state의 clock은 재생 위치(녹화 시작 후 초)다. 그래서 수신 속도와 연결 유지 시간도 녹화 기준으로 나온다.
뒤로 이동하면 처음부터 다시 넣는다(10분 녹화 기준 1초 안팎, 추정).
"""

from __future__ import annotations

import bisect
from typing import List, Optional, Tuple

from aesa_monitor.core.events import Event
from aesa_monitor.core.protocol import Beam, MonitorStatus
from aesa_monitor.core.state import MonitorState
from aesa_monitor.io.recording import Recording

SPEEDS = (0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0)


class ReplayPlayer:
    def __init__(self, recording: Recording) -> None:
        self.recording = recording
        self.events, self.decode_errors = recording.events()
        self._times = [time_s for time_s, _ in self.events]
        self.duration_s = self._times[-1] if self._times else 0.0
        self.position_s = 0.0
        self.speed = 1.0
        self.playing = False
        self._index = 0
        self.state = self._new_state()
        # 처리부 시각 → 녹화 시각 대응표. 처리부가 다시 시작하면 구간이 나뉜다.
        self._processor_index: List[Tuple[int, float]] = []
        for time_s, event in self.events:
            processor_ms = _processor_time(event)
            if processor_ms is not None:
                self._processor_index.append((processor_ms, time_s))

    def _new_state(self) -> MonitorState:
        return MonitorState(clock=lambda: self.position_s)

    # 조작

    def play(self) -> None:
        if self.position_s >= self.duration_s:
            self.seek(0.0)
        self.playing = True

    def pause(self) -> None:
        self.playing = False

    def toggle(self) -> None:
        if self.playing:
            self.pause()
        else:
            self.play()

    def seek(self, position_s: float) -> None:
        position_s = min(max(position_s, 0.0), self.duration_s)
        if position_s < self.position_s:
            self.state = self._new_state()
            self._index = 0
        self.position_s = position_s
        self._apply_until(position_s)

    def advance(self, wall_seconds: float) -> None:
        """화면 갱신 사이에 흐른 실제 시간만큼 재생한다."""
        if not self.playing:
            return
        target = self.position_s + wall_seconds * self.speed
        if target >= self.duration_s:
            target = self.duration_s
            self.playing = False
        self.position_s = target
        self._apply_until(target)

    def _apply_until(self, position_s: float) -> None:
        end = bisect.bisect_right(self._times, position_s)
        state = self.state
        for index in range(self._index, end):
            time_s, event = self.events[index]
            state.apply(event, time_s)
        self._index = max(self._index, end)

    # 처리부 시각

    def processor_range_ms(self) -> Optional[Tuple[int, int]]:
        if not self._processor_index:
            return None
        values = [value for value, _ in self._processor_index]
        return min(values), max(values)

    def time_for_processor_ms(self, processor_ms: int) -> Optional[float]:
        """처리부 시각이 processor_ms에 처음 도달한 녹화 시각."""
        for value, time_s in self._processor_index:
            if value >= processor_ms:
                return time_s
        return None

    def first_beam_s(self) -> float:
        for time_s, event in self.events:
            if isinstance(event, Beam):
                return time_s
        return 0.0

    def event_count(self) -> int:
        return len(self.events)


def _processor_time(event: Event) -> Optional[int]:
    if isinstance(event, Beam):
        return event.timestamp_ms
    if isinstance(event, MonitorStatus):
        return event.processor_time_ms
    return None
