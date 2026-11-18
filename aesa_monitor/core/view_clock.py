"""실시간 화면의 표시 시각.

통신 캡처(특히 Windows의 Npcap·dumpcap)는 패킷을 모아서 넘긴다. 실제 녹화에서 메시지가 35~50개씩
100~250 ms마다 한꺼번에 들어왔다. 받은 마지막 시각을 그대로 '지금'으로 그리면 화면이 그 간격만큼 멈췄다가
건너뛴다(빔 탭이 끊기며 움직임).

ViewClock은 표시 시각을 벽시계 흐름(처리부 시간 배율 반영)대로 고르게 진행시키고, 받은 마지막 시각보다
LAG_MS만큼 뒤에 머물도록 조금씩 맞춘다. 데이터가 LAG_MS 안에 도착하면 화면은 10 ms 단위로 고르게 움직인다.
처리부 시각이 크게 바뀌면(운용 OFF→ON, 처리부 재시작, 오래 늦어짐) 그 시각으로 바로 옮긴다.
"""

from __future__ import annotations

from typing import Optional

# 표시 시각을 받은 마지막 시각보다 늦추는 정도. 캡처 묶음 간격(대개 100~150 ms)보다 커야 고르게 보인다.
LAG_MS = 200
# 목표에서 이만큼 넘게 벗어나면 따라가지 않고 바로 옮긴다.
JUMP_MS = 1000
# 갱신마다 목표와의 차이를 줄이는 비율(갱신 20 Hz에서 약 0.5 s에 걸쳐 맞춤)
CORRECTION = 0.1


class ViewClock:
    def __init__(self, lag_ms: int = LAG_MS, jump_ms: int = JUMP_MS, correction: float = CORRECTION) -> None:
        self.lag_ms = lag_ms
        self.jump_ms = jump_ms
        self.correction = correction
        self._view: Optional[float] = None
        self._wall: Optional[float] = None

    def reset(self) -> None:
        self._view = None
        self._wall = None

    def update(self, latest_ms: Optional[int], wall_s: float, scale: float = 1.0) -> Optional[int]:
        """받은 마지막 처리부 시각(latest_ms)과 지금 벽시계(초)로 표시 시각을 정한다."""
        if latest_ms is None:
            self.reset()
            return None
        target = latest_ms - self.lag_ms
        if self._view is None or self._wall is None:
            view = float(target)
        else:
            view = self._view + max(wall_s - self._wall, 0.0) * 1000.0 * max(scale, 0.0)
            if abs(target - view) > self.jump_ms:
                view = float(target)
            else:
                view += (target - view) * self.correction
        view = min(view, float(latest_ms))  # 아직 받지 않은 시각은 그리지 않는다
        self._view, self._wall = view, wall_s
        return int(view)
