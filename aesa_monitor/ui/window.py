"""시작 창 크기 계산. tkinter 없이 테스트할 수 있게 화면 크기만 받는다."""

from __future__ import annotations

from dataclasses import dataclass

# 글자 16에서도 빔 탭의 요청 빔 일정 표 4행과 탐색 프레임별 빔 수 막대가 다 보이는 크기(클라우드 화면 측정 기준).
PREFERRED_SIZE = (1680, 1000)
MINIMUM_SIZE = (1100, 720)
# 창 제목 줄·작업 표시줄·창 테두리 몫으로 화면에서 빼는 너비·높이(px, 추정값).
SCREEN_MARGIN = (40, 100)


@dataclass(frozen=True)
class WindowSize:
    width: int
    height: int
    x: int
    y: int
    maximize: bool          # 원하는 크기가 화면에 다 들어가지 않으면 최대화로 시작한다
    min_width: int
    min_height: int

    @property
    def geometry(self) -> str:
        return f"{self.width}x{self.height}+{self.x}+{self.y}"


def initial_window_size(screen_width: int, screen_height: int) -> WindowSize:
    """화면에 PREFERRED_SIZE가 들어가면 그 크기로 가운데에, 아니면 쓸 수 있는 만큼 잡고 최대화한다.

    최소 크기도 화면을 넘지 않게 줄인다(1366×768 노트북에서 최소 높이 720이 화면보다 커지지 않게).
    """
    usable_width = max(screen_width - SCREEN_MARGIN[0], 640)
    usable_height = max(screen_height - SCREEN_MARGIN[1], 480)
    fits = PREFERRED_SIZE[0] <= usable_width and PREFERRED_SIZE[1] <= usable_height
    width = min(PREFERRED_SIZE[0], usable_width)
    height = min(PREFERRED_SIZE[1], usable_height)
    x = max((screen_width - width) // 2, 0)
    y = max((screen_height - height) // 2 - 20, 0)
    return WindowSize(width, height, x, y, not fits,
                      min(MINIMUM_SIZE[0], usable_width), min(MINIMUM_SIZE[1], usable_height))
