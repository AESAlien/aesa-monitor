"""색, 글꼴, 표시 형식.

글꼴은 이름 붙은 글꼴(named font)로 만든다. 크기를 바꾸면 모든 화면 글자가 한 번에 바뀐다(Ctrl +/-).
캔버스 글자도 같은 글꼴 이름을 쓴다.

색 원칙: LIG CI 색(파랑·회색·검정)은 화면 틀(상단 띠, 선택된 탭, 버튼, 테두리, 바탕)에만 쓴다.
상태 색(정상·주의·이상)과 데이터 색(빔 종류, 트랙, 예측·탐지)은 CI와 상관없이 의미를 지키는 색을 쓴다.
"""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from tkinter import ttk
from typing import Optional

from aesa_monitor.core.protocol import BeamType, TrackStatus


def _mix(color: str, ratio: float, background: str) -> str:
    """color를 ratio만큼 background 위에 섞은 색 (#rrggbb)."""
    ratio = min(max(ratio, 0.0), 1.0)
    channels = []
    for index in (1, 3, 5):
        foreground = int(color[index:index + 2], 16)
        back = int(background[index:index + 2], 16)
        channels.append(round(back + (foreground - back) * ratio))
    return "#" + "".join(f"{value:02x}" for value in channels)


# LIG CI 전용 색 (LIG CI 매뉴얼 2021-11-05, https://www.ligs.co.kr/lib/CIManual_20211105.pdf)
LIG_BLUE = "#00377b"   # LIG Innovative Blue, PANTONE 288C, R0 G55 B123
LIG_GRAY = "#bcbec0"   # LIG Futuristic Gray, PANTONE CoolGray 4C, R188 G190 B192
LIG_BLACK = "#231f20"  # Black, PANTONE Black C, R35 G31 B32

BACKGROUND = "#ffffff"
TEXT = LIG_BLACK
# 화면 틀 강조색. 흰 글자 대비 약 11:1. 탐색 빔(#378add)보다 훨씬 어두워 명도로 구분된다.
ACCENT = LIG_BLUE
ACCENT_ACTIVE = _mix(LIG_BLUE, 0.8, BACKGROUND)  # 버튼 누름. CI 원색이 아니라 CI 파랑을 밝힌 색
ACCENT_SOFT = _mix(LIG_BLUE, 0.12, BACKGROUND)   # 탭·버튼에 마우스를 올렸을 때, 표에서 고른 행
# CI 회색은 흰 바탕 대비 약 1.9:1이라 글자색으로는 쓰지 않고 테두리·구분선에만 쓴다.
BORDER = LIG_GRAY
GRID_LINE = _mix(LIG_GRAY, 0.6, BACKGROUND)      # 캔버스 격자. 원색은 데이터보다 눈에 띄어 옅게 쓴다
PANEL = _mix(LIG_BLUE, 0.05, BACKGROUND)         # 카드·막대 바탕. CI 파랑을 아주 옅게
# 보조 글자. CI 회색은 대비가 모자라서 푸른 기가 도는 진한 회색을 따로 둔다(흰 바탕 대비 약 6:1).
MUTED = "#5a6068"

OK = "#2f7d32"
WARN = "#b26a00"
BAD = "#b3261e"
UNKNOWN = "#80868e"
LEVEL_COLORS = {"ok": OK, "warn": WARN, "bad": BAD, "idle": UNKNOWN}
LEVEL_BACKGROUNDS = {"ok": "#e9f4e9", "warn": "#fff2df", "bad": "#fde9e7", "idle": PANEL}
LEVEL_NAMES = {"ok": "정상", "warn": "주의", "bad": "이상", "idle": "대기"}

# 빔 종류 색은 sim_gui와 같게 맞춘다: 탐색 파랑, 확인 노랑, 추적 초록.
BEAM_COLORS = {
    BeamType.SEARCH: "#378add",
    BeamType.CONFIRMATION: "#d9a100",
    BeamType.TRACKING: "#1d9e75",
}
# 판정 수준이 없는 그래프(분포 막대 등)의 기본 색. 탐색 빔 색과 같다.
CHART_COLOR = BEAM_COLORS[BeamType.SEARCH]
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
CANDIDATE_COLOR = "#a4a8ae"  # 중립 회색이라 바탕색에 맞춰 푸른 회색으로 (밝기는 이전 #a8a69c와 비슷)

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
        # clam 테마는 선택된 탭의 padding을 따로 줄여 두므로 같은 값으로 덮는다(안 하면 선택 탭만 좁아진다).
        style.map("TNotebook.Tab", padding=[("selected", (14, 6))])
        style.configure("TButton", font=FONT_BODY, padding=(8, 3))
        style.configure("TCombobox", font=FONT_BODY)
        style.configure("TCheckbutton", font=FONT_BODY, background=BACKGROUND)
        style.configure("TRadiobutton", font=FONT_BODY, background=BACKGROUND)

    def line_height(self, name: str = FONT_BODY) -> int:
        return self._fonts[name].metrics("linespace")

    def measure(self, text: str, name: str = FONT_BODY) -> int:
        return self._fonts[name].measure(text)


def apply_theme(root: tk.Misc) -> None:
    """ttk 위젯에 CI 색을 입힌다. Fonts보다 먼저 불러야 한다(theme_use가 앞선 설정을 지운다).

    기본(default) 테마는 탭·버튼 색 지정을 일부 무시해서 clam 테마를 쓴다. clam이 없으면 색 없이 둔다.
    표에서 고른 행은 옅은 파랑 바탕에 원래 글자색을 둔다. 진한 파랑 바탕이면 상태 색 글자(초록·빨강)가 안 읽힌다.
    """
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        return
    style.configure(".", background=BACKGROUND, foreground=TEXT, bordercolor=BORDER, lightcolor=BACKGROUND,
                    darkcolor=BORDER, troughcolor=PANEL, focuscolor=ACCENT, selectbackground=ACCENT_SOFT,
                    selectforeground=TEXT, insertcolor=TEXT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TNotebook", background=BACKGROUND, bordercolor=BORDER, lightcolor=BACKGROUND,
                    darkcolor=BORDER)
    style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, bordercolor=BORDER, lightcolor=PANEL)
    style.map("TNotebook.Tab",
              background=[("selected", ACCENT), ("active", ACCENT_SOFT)],
              foreground=[("selected", BACKGROUND), ("active", TEXT)],
              lightcolor=[("selected", ACCENT), ("active", ACCENT_SOFT)],
              bordercolor=[("selected", ACCENT)])
    style.configure("TButton", background=PANEL, foreground=TEXT, bordercolor=BORDER, lightcolor=PANEL,
                    darkcolor=PANEL)
    style.map("TButton",
              background=[("disabled", BACKGROUND), ("pressed", ACCENT_ACTIVE), ("active", ACCENT_SOFT)],
              foreground=[("disabled", UNKNOWN), ("pressed", BACKGROUND)],
              lightcolor=[("pressed", ACCENT_ACTIVE), ("active", ACCENT_SOFT)],
              darkcolor=[("pressed", ACCENT_ACTIVE), ("active", ACCENT_SOFT)],
              bordercolor=[("focus", ACCENT), ("active", ACCENT)])
    style.configure("Treeview", background=BACKGROUND, fieldbackground=BACKGROUND, foreground=TEXT,
                    bordercolor=BORDER, lightcolor=BACKGROUND, darkcolor=BACKGROUND)
    # 고른 행 글자색은 지정하지 않는다(foreground 맵 비움). 그래야 행 태그의 상태 색(초록·주황·빨강)이 고른 뒤에도 남는다.
    style.map("Treeview", background=[("selected", ACCENT_SOFT)], foreground=[])
    style.configure("Treeview.Heading", background=PANEL, foreground=TEXT, bordercolor=BORDER, lightcolor=PANEL,
                    darkcolor=PANEL)
    style.map("Treeview.Heading", background=[("active", ACCENT_SOFT)], lightcolor=[("active", ACCENT_SOFT)])
    style.configure("TCombobox", fieldbackground=BACKGROUND, background=PANEL, foreground=TEXT, arrowcolor=TEXT,
                    bordercolor=BORDER, lightcolor=BACKGROUND, darkcolor=BACKGROUND)
    style.map("TCombobox",
              fieldbackground=[("readonly", BACKGROUND)],
              selectbackground=[("readonly", BACKGROUND)],
              selectforeground=[("readonly", TEXT)],
              bordercolor=[("focus", ACCENT)],
              background=[("active", ACCENT_SOFT)])
    style.configure("TEntry", fieldbackground=BACKGROUND, bordercolor=BORDER, lightcolor=BACKGROUND,
                    darkcolor=BACKGROUND)
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
    for name in ("TCheckbutton", "TRadiobutton"):
        style.configure(name, background=BACKGROUND, foreground=TEXT, indicatorbackground=BACKGROUND,
                        indicatorforeground=ACCENT, upperbordercolor=BORDER, lowerbordercolor=BORDER)
        style.map(name, background=[("active", BACKGROUND)],
                  indicatorbackground=[("pressed", ACCENT_SOFT), ("active", ACCENT_SOFT)])
    style.configure("TScrollbar", background=PANEL, troughcolor=BACKGROUND, bordercolor=BORDER, arrowcolor=MUTED,
                    lightcolor=PANEL, darkcolor=PANEL)
    style.map("TScrollbar", background=[("active", ACCENT_SOFT)])
    style.configure("Horizontal.TScale", background=ACCENT, troughcolor=PANEL, bordercolor=BORDER,
                    lightcolor=ACCENT, darkcolor=ACCENT)
    style.map("Horizontal.TScale", background=[("active", ACCENT_ACTIVE)])


def track_color(track_id: int) -> str:
    return TRACK_COLORS[(track_id - 1) % len(TRACK_COLORS)]


def blend(color: str, ratio: float, background: str = PANEL) -> str:
    """color를 ratio만큼 background 위에 섞은 색을 반환한다."""
    return _mix(color, ratio, background)


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
