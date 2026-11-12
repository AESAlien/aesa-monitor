"""메시지 종류별 수신 횟수와 수신 속도."""

from __future__ import annotations

import time
from collections import deque
from typing import Callable, Deque, Dict, Optional

from aesa_monitor.core.protocol import (
    Beam, Detection, MessageId, MonitorStatus, OperatorControl, RadarAttitude, SimulatorAttitude, TrackDeletion,
    TrackList,
)

RATE_WINDOW_SECONDS = 1.0

MESSAGE_TYPES = {
    Beam: MessageId.BEAM_TRANSMIT,
    TrackList: MessageId.TRACK_INFORMATION,
    Detection: MessageId.PLOT_INFORMATION,
    RadarAttitude: MessageId.RADAR_ATTITUDE_TO_CONSOLE,
    MonitorStatus: MessageId.MONITOR_STATUS,
    OperatorControl: MessageId.OPERATOR_CONTROL,
    SimulatorAttitude: MessageId.RADAR_ATTITUDE_FROM_SIMULATOR,
    TrackDeletion: MessageId.TRACK_DELETION,
}

# 통신 캡처에서만 보이는 메시지
CAPTURE_ONLY_MESSAGES = (
    MessageId.OPERATOR_CONTROL, MessageId.RADAR_ATTITUDE_FROM_SIMULATOR, MessageId.TRACK_DELETION,
)

MESSAGE_NAMES = {
    MessageId.BEAM_TRANSMIT: "빔 송신 명령",
    MessageId.PLOT_INFORMATION: "탐지 정보",
    MessageId.TRACK_INFORMATION: "트랙 정보",
    MessageId.RADAR_ATTITUDE_TO_CONSOLE: "레이더 자세",
    MessageId.MONITOR_STATUS: "모니터 상태",
    MessageId.OPERATOR_CONTROL: "운용 명령",
    MessageId.RADAR_ATTITUDE_FROM_SIMULATOR: "모의기 자세",
    MessageId.TRACK_DELETION: "트랙 삭제",
}

MESSAGE_ROUTES = {
    MessageId.BEAM_TRANSMIT: "처리부 → 모의기",
    MessageId.PLOT_INFORMATION: "모의기 → 처리부",
    MessageId.TRACK_INFORMATION: "처리부 → 운용통제기",
    MessageId.RADAR_ATTITUDE_TO_CONSOLE: "처리부 → 운용통제기",
    MessageId.MONITOR_STATUS: "처리부 → 모니터",
    MessageId.OPERATOR_CONTROL: "운용통제기 → 처리부",
    MessageId.RADAR_ATTITUDE_FROM_SIMULATOR: "모의기 → 처리부",
    MessageId.TRACK_DELETION: "처리부 → 운용통제기",
}


class MessageTraffic:
    """받은 메시지를 종류별로 세고 최근 1초 수신 속도를 계산한다.

    시각은 clock이 주는 초 단위 값이다. 실시간에서는 모니터의 단조 시계, 재생에서는 녹화 시각이다.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.counts: Dict[MessageId, int] = {message_id: 0 for message_id in MESSAGE_TYPES.values()}
        self.last_received: Dict[MessageId, float] = {}
        self._recent: Dict[MessageId, Deque[float]] = {
            message_id: deque() for message_id in MESSAGE_TYPES.values()
        }

    def clear(self) -> None:
        self.__init__(self._clock)

    def record(self, message: object, received_at: Optional[float] = None) -> None:
        message_id = MESSAGE_TYPES.get(type(message))
        if message_id is None:
            return
        now = self._clock() if received_at is None else received_at
        self.counts[message_id] += 1
        self.last_received[message_id] = now
        recent = self._recent[message_id]
        recent.append(now)
        self._trim(recent, now)

    def rate(self, message_id: MessageId, now: Optional[float] = None) -> float:
        """최근 1초 동안 받은 개수(개/초)."""
        current = self._clock() if now is None else now
        recent = self._recent[message_id]
        self._trim(recent, current)
        return len(recent) / RATE_WINDOW_SECONDS

    def age(self, message_id: MessageId, now: Optional[float] = None) -> Optional[float]:
        """마지막으로 받은 뒤 지난 초. 받은 적이 없으면 None이다."""
        last = self.last_received.get(message_id)
        if last is None:
            return None
        return (self._clock() if now is None else now) - last

    @staticmethod
    def _trim(recent: Deque[float], now: float) -> None:
        while recent and now - recent[0] > RATE_WINDOW_SECONDS:
            recent.popleft()
