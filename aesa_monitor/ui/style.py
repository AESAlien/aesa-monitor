"""색, 글꼴, 표시 형식.

글꼴은 이름 붙은 글꼴(named font)로 만든다. 크기를 바꾸면 모든 화면 글자가 한 번에 바뀐다(Ctrl +/-).
캔버스 글자도 같은 글꼴 이름을 쓴다.
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Optional

from aesa_monitor.core.protocol import BeamType, TrackStatus

BACKGROUND = "#ffffff"
PANEL = "#f4f2ec"
GRID_LINE = "#d3d1c7"
TEXT = "#1f1f1d"
MUTED = "#5f5e5a"

OK = "#2f7d32"
WARN = "#b26a00"
BAD = "#b3261e"
UNKNOWN = "#888780"
LEVEL_COLORS = {"ok": OK, "warn": WARN, "bad": BAD, "idle": UNKNOWN}
LEVEL_BACKGROUNDS = {"ok": "#e9f4e9", "warn": "#fff2df", "bad": "#fde9e7", "idle": PANEL}
LEVEL_NAMES = {"ok": "정상", "warn": "주의", "bad": "이상", "idle": "대기"}

# 빔 종류 색은 sim_gui와 같게 맞춘다: 탐색 파랑, 확인 노랑, 추적 초록.
BEAM_COLORS = {
    BeamType.SEARCH: "#378add",
    BeamType.CONFIRMATION: "#d9a100",
    BeamType.TRACKING: "#1d9e75",
}
BEAM_NAMES = {
    BeamType.SEARCH: "탐색",
    BeamType.CONFIRMATION: "확인",
    BeamType.TRACKING: "추적",
}
TRACK_STATUS_NAMES = {
    TrackStatus.INIT: "초기",
    TrackStatus.TRACKING: "추적",
}

# 예측(처리부 계산), 실제 탐지(모의기), 갱신 결과는 모든 탭에서 같은 색으로 쓴다.
PREDICTED_COLOR = "#6250d6"
DETECTED_COLOR = "#e0601f"
ESTIMATE_COLOR = "#1d9e75"
CANDIDATE_COLOR = "#a8a69c"

# 트랙 ID별 색. 8개를 넘으면 반복되므로 ID 글자를 항상 함께 쓴다.
TRACK_COLORS = ("#2a78d6", "#e0601f", "#1d9e75", "#c98d00", "#d4508a", "#3d8b00", "#6250d6", "#c7362f")

FONT_BODY = "AesaBody"
FONT_SMALL = "AesaSmall"
FONT_TITLE = "AesaTitle"
FONT_VALUE = "AesaValue"
FONT_VERDICT = "AesaVerdict"
FONT_FIXED = "AesaFixed"
FONT_BOLD = "AesaBold"
DEFAULT_FONT_SIZE = 12
MIN_FONT_SIZE = 8
MAX_FONT_SIZE = 24


class Fonts:
    """화면 전체 글꼴. size는 본문 글자 크기(pt)다."""

    def __init__(self, root: tk.Misc, size: int = DEFAULT_FONT_SIZE) -> None:
        self._root = root
        self.size = size
        family = tkfont.nametofont("TkDefaultFont").actual("family")
        fixed = tkfont.nametofont("TkFixedFont").actual("family")
        self._fonts = {
            FONT_BODY: tkfont.Font(root, name=FONT_BODY, family=family, exists=False),
            FONT_SMALL: tkfont.Font(root, name=FONT_SMALL, family=family, exists=False),
            FONT_TITLE: tkfont.Font(root, name=FONT_TITLE, family=family, weight="bold", exists=False),
            FONT_VALUE: tkfont.Font(root, name=FONT_VALUE, family=family, weight="bold", exists=False),
            FONT_VERDICT: tkfont.Font(root, name=FONT_VERDICT, family=family, weight="bold", exists=False),
            FONT_FIXED: tkfont.Font(root, name=FONT_FIXED, family=fixed, exists=False),
            FONT_BOLD: tkfont.Font(root, name=FONT_BOLD, family=family, weight="bold", exists=False),
        }
        self.apply(size)

    def apply(self, size: int) -> None:
        size = int(min(max(size, MIN_FONT_SIZE), MAX_FONT_SIZE))
        self.size = size
        sizes = {
            FONT_BODY: size,
            FONT_SMALL: max(size - 2, MIN_FONT_SIZE),
            FONT_TITLE: size + 1,
            FONT_VALUE: round(size * 1.75),
            FONT_VERDICT: size + 3,
            FONT_FIXED: max(size - 1, MIN_FONT_SIZE),
            FONT_BOLD: size,
        }
        for name, font in self._fonts.items():
            font.configure(size=sizes[name])
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont", "TkCaptionFont"):
            try:
                tkfont.nametofont(name).configure(size=size)
            except tk.TclError:
                pass
        style = ttk.Style(self._root)
        line = self._fonts[FONT_BODY].metrics("linespace")
        style.configure("Treeview", rowheight=int(line * 1.45), font=FONT_BODY)
        style.configure("Treeview.Heading", font=FONT_BOLD)
        style.configure("TNotebook.Tab", font=FONT_TITLE, padding=(14, 6))
        style.configure("TButton", font=FONT_BODY, padding=(8, 3))
        style.configure("TCombobox", font=FONT_BODY)
        style.configure("TCheckbutton", font=FONT_BODY, background=BACKGROUND)
        style.configure("TRadiobutton", font=FONT_BODY, background=BACKGROUND)

    def line_height(self, name: str = FONT_BODY) -> int:
        return self._fonts[name].metrics("linespace")

    def measure(self, text: str, name: str = FONT_BODY) -> int:
        return self._fonts[name].measure(text)


def track_color(track_id: int) -> str:
    return TRACK_COLORS[(track_id - 1) % len(TRACK_COLORS)]


def blend(color: str, ratio: float, background: str = PANEL) -> str:
    """color를 ratio만큼 background 위에 섞은 색을 반환한다."""
    ratio = min(max(ratio, 0.0), 1.0)
    channels = []
    for index in (1, 3, 5):
        foreground = int(color[index:index + 2], 16)
        back = int(background[index:index + 2], 16)
        channels.append(round(back + (foreground - back) * ratio))
    return "#" + "".join(f"{value:02x}" for value in channels)


def beam_color(beam_type: int) -> str:
    try:
        return BEAM_COLORS[BeamType(beam_type)]
    except ValueError:
        return TEXT


def beam_name(beam_type: int) -> str:
    try:
        return BEAM_NAMES[BeamType(beam_type)]
    except ValueError:
        return str(beam_type)


def track_status_name(status: int) -> str:
    try:
        return TRACK_STATUS_NAMES[TrackStatus(status)]
    except ValueError:
        return str(status)


def format_time(milliseconds: Optional[float]) -> str:
    if milliseconds is None:
        return "--:--.---"
    milliseconds = int(round(milliseconds))
    sign = "-" if milliseconds < 0 else ""
    minutes, remainder = divmod(abs(milliseconds), 60000)
    return f"{sign}{minutes:02d}:{remainder / 1000:06.3f}"


def format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "-"
    seconds = max(seconds, 0.0)
    if seconds < 60:
        return f"{seconds:.0f}초"
    minutes, rest = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}분 {rest:02d}초"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}시간 {minutes:02d}분"


def format_scale(scale: float) -> str:
    return f"{scale:.2f}".rstrip("0").rstrip(".")


def format_number(value: Optional[float], digits: int = 1, unit: str = "") -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "-"
    return f"{value:.{digits}f}{unit}"


def format_bytes(count: int) -> str:
    if count < 1024 * 1024:
        return f"{count / 1024:.0f} KB"
    return f"{count / 1024 / 1024:.1f} MB"
