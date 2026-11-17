"""여러 탭이 함께 쓰는 화면 부품: 판정 줄, 판정 카드 줄, 표, 도움말, 툴팁, 캔버스 라벨 배치, 그래프."""

from __future__ import annotations

import math
import tkinter as tk
import tkinter.font as tkfont
from collections import Counter
from tkinter import ttk
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from aesa_monitor.core.frames import FrameShare
from aesa_monitor.core.state import BEAM_SLOT_MS
from aesa_monitor.core.protocol import BeamType
from aesa_monitor.core.verdict import Verdict
from aesa_monitor.ui.style import (
    BACKGROUND, BEAM_COLORS, BEAM_NAMES, FONT_BODY, FONT_SMALL, FONT_TITLE, FONT_VALUE, FONT_VERDICT, GRID_LINE, LEVEL_BACKGROUNDS,
    CHART_COLOR, LEVEL_COLORS, LEVEL_NAMES, MUTED, PANEL, TEXT, TOOLTIP_BACKGROUND, blend,
)

Box = Tuple[float, float, float, float]


class VerdictBar:
    """탭 맨 위의 판정 한 줄: [● 정상] 설명."""

    def __init__(self, parent: tk.Widget, question: str) -> None:
        self.frame = tk.Frame(parent, background=PANEL, padx=12, pady=8)
        self._badge = tk.Label(self.frame, text="● 대기", font=FONT_VERDICT, background=PANEL,
                               foreground=LEVEL_COLORS["idle"])
        self._badge.pack(side=tk.LEFT)
        self._text = tk.Label(self.frame, text="", font=FONT_TITLE, background=PANEL, foreground=TEXT,
                              anchor="w", justify=tk.LEFT)
        self._text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(12, 0))
        self._question = tk.Label(self.frame, text=question, font=FONT_SMALL, background=PANEL,
                                  foreground=MUTED)
        self._question.pack(side=tk.RIGHT)
        self.frame.bind("<Configure>", self._wrap)
        self._last: Tuple[str, str] = ("", "")

    def _wrap(self, event: tk.Event) -> None:
        width = event.width - self._badge.winfo_width() - self._question.winfo_width() - 60
        self._text.configure(wraplength=max(width, 200))

    def set(self, verdict: Verdict) -> None:
        key = (verdict.level, verdict.text)
        if key == self._last:
            return
        self._last = key
        background = LEVEL_BACKGROUNDS[verdict.level]
        for widget in (self.frame, self._badge, self._text, self._question):
            widget.configure(background=background)
        self._badge.configure(text=f"● {LEVEL_NAMES[verdict.level]}", foreground=LEVEL_COLORS[verdict.level])
        self._text.configure(text=verdict.text)


LEVEL_SYMBOLS = {"ok": "●", "warn": "▲", "bad": "■", "idle": "●"}


class StatusCard:
    """판정 카드 줄의 지표 하나: 제목, 큰 값, 기준 설명.

    정상·대기면 무채색으로 두고, 주의·이상이면 이 카드만 바탕·제목·값에 판정 색과 기호(▲ 주의, ■ 이상)를 넣는다.
    색약이나 흑백 출력에서도 구분되게 색과 기호를 함께 쓴다.
    """

    def __init__(self, parent: tk.Widget, title: str, help_text: str = "") -> None:
        self.title = title
        self.level = "idle"
        self.value_text = "-"
        self.frame = tk.Frame(parent, background=PANEL)
        self._stripe = tk.Frame(self.frame, background=PANEL, width=6)
        self._stripe.pack(side=tk.LEFT, fill=tk.Y)
        self._body = tk.Frame(self.frame, background=PANEL, padx=10, pady=6)
        self._body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        header = tk.Frame(self._body, background=PANEL)
        header.pack(fill=tk.X)
        self._title = tk.Label(header, text=title, font=FONT_BODY, background=PANEL, foreground=MUTED, anchor="w")
        self._title.pack(side=tk.LEFT)
        self._help = HelpButton(header, title, help_text, background=PANEL) if help_text else None
        if self._help is not None:
            self._help.pack(side=tk.RIGHT)
        self._value = tk.Label(self._body, text="-", font=FONT_VALUE, background=PANEL, foreground=TEXT, anchor="w")
        self._value.pack(fill=tk.X)
        self._detail = tk.Label(self._body, text="", font=FONT_SMALL, background=PANEL, foreground=MUTED,
                                anchor="w", justify=tk.LEFT)
        self._detail.pack(fill=tk.X)
        self._backgrounds = (self.frame, self._body, header, self._title, self._value, self._detail)
        self._last: Tuple = ()
        self.compact = False
        # 낮은 화면에서 아랫줄(기준 설명)을 숨기면 마우스를 올려 볼 수 있게 한다.
        Tooltip(self._value, lambda: self._detail.cget("text") if self.compact else "")

    def set_compact(self, compact: bool) -> None:
        """낮은 화면용: 아랫줄(기준 설명)을 숨겨 카드 높이를 줄인다(툴팁에 남음)."""
        if compact == self.compact:
            return
        self.compact = compact
        if compact:
            self._detail.pack_forget()
        else:
            self._detail.pack(fill=tk.X)

    def set(self, value: str, detail: str = "", level: str = "idle") -> None:
        key = (value, detail, level)
        if key == self._last:
            return
        self._last = key
        self.level, self.value_text = level, value
        problem = level in ("warn", "bad")
        background = LEVEL_BACKGROUNDS[level] if problem else PANEL
        for widget in self._backgrounds:
            widget.configure(background=background)
        self._stripe.configure(background=LEVEL_COLORS[level] if problem else PANEL)
        self._title.configure(foreground=LEVEL_COLORS[level] if problem else MUTED)
        self._value.configure(text=f"{LEVEL_SYMBOLS[level]} {value}" if problem else value,
                              foreground=LEVEL_COLORS[level] if problem else TEXT)
        self._detail.configure(text=detail, foreground=TEXT if problem else MUTED)


class VerdictCardRow:
    """탭 맨 위 판정과 지표 카드를 한 줄로 합친 것: [판정 배지] [지표 카드 …].

    - 배지: 탭 전체 판정(지표 중 가장 나쁜 값)과 탭의 질문.
    - 지표 카드: 문제인 카드만 색이 들어온다(StatusCard).
    - 판정이 정상이 아닌데 문제인 카드가 없으면 탭 전체 상태(연결 끊김, 처리부 정지, 빔 없음 등)다.
      이때는 카드 대신 판정 문구와 마지막 값을 보여준다.
    set()보다 카드 값(add_card로 받은 StatusCard.set)을 먼저 갱신해야 한다.
    """

    def __init__(self, parent: tk.Widget, question: str) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND)
        self._badge = tk.Frame(self.frame, background=LEVEL_COLORS["idle"], padx=14, pady=6)
        self._badge.pack(side=tk.LEFT, fill=tk.Y, padx=(0, 8))
        self._badge_text = tk.Label(self._badge, text="● 대기", font=FONT_VERDICT, background=LEVEL_COLORS["idle"],
                                    foreground=BACKGROUND, anchor="w", width=6)
        self._badge_text.pack(fill=tk.X, expand=True)
        self._question = tk.Label(self._badge, text=question, font=FONT_SMALL, background=LEVEL_COLORS["idle"],
                                  foreground=BACKGROUND, anchor="w", justify=tk.LEFT)
        self._question.pack(fill=tk.X)
        self._cards = tk.Frame(self.frame, background=BACKGROUND)
        self._cards.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._message_frame = tk.Frame(self.frame, background=LEVEL_BACKGROUNDS["idle"], padx=14, pady=6)
        self._message = tk.Label(self._message_frame, text="", font=FONT_VERDICT, background=LEVEL_BACKGROUNDS["idle"],
                                 foreground=TEXT, anchor="w", justify=tk.LEFT)
        self._message.pack(fill=tk.X)
        self._last_values = tk.Label(self._message_frame, text="", font=FONT_SMALL,
                                     background=LEVEL_BACKGROUNDS["idle"], foreground=MUTED, anchor="w",
                                     justify=tk.LEFT)
        self._last_values.pack(fill=tk.X, pady=(4, 0))
        self._message_frame.bind("<Configure>", self._wrap)
        self.cards: List[StatusCard] = []
        self._showing_message = False
        self._last: Tuple = ()

    def set_compact(self, compact: bool) -> None:
        """낮은 화면용: 카드 아랫줄과 배지의 질문을 숨겨 줄 높이를 줄인다."""
        for card in self.cards:
            card.set_compact(compact)
        if compact:
            self._question.pack_forget()
        elif not self._question.winfo_manager():
            self._question.pack(fill=tk.X)

    @property
    def compact(self) -> bool:
        return bool(self.cards) and self.cards[0].compact

    def _wrap(self, event: tk.Event) -> None:
        width = max(event.width - 30, 200)
        self._message.configure(wraplength=width)
        self._last_values.configure(wraplength=width)

    def add_card(self, title: str, help_text: str = "") -> StatusCard:
        card = StatusCard(self._cards, title, help_text)
        index = len(self.cards)
        card.frame.grid(row=0, column=index, sticky="nsew", padx=(0 if index == 0 else 8, 0))
        self._cards.columnconfigure(index, weight=1, uniform="status")
        self._cards.rowconfigure(0, weight=1)
        self.cards.append(card)
        return card

    def set(self, verdict: Verdict) -> None:
        level = verdict.level
        message = level != "ok" and not any(card.level in ("warn", "bad") for card in self.cards)
        last_values = " · ".join(f"{card.title} {card.value_text}" for card in self.cards
                                 if card.value_text not in ("", "-"))
        key = (level, verdict.text, message, last_values if message else "")
        if key == self._last:
            return
        self._last = key
        color = LEVEL_COLORS[level]
        for widget in (self._badge, self._badge_text, self._question):
            widget.configure(background=color)
        self._badge_text.configure(text=f"{LEVEL_SYMBOLS[level]} {LEVEL_NAMES[level]}")
        if message:
            background = LEVEL_BACKGROUNDS[level]
            for widget in (self._message_frame, self._message, self._last_values):
                widget.configure(background=background)
            self._message.configure(text=verdict.text)
            self._last_values.configure(text=f"마지막 값  {last_values}" if last_values else "")
        if message != self._showing_message:
            self._showing_message = message
            if message:
                self._cards.pack_forget()
                self._message_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
            else:
                self._message_frame.pack_forget()
                self._cards.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)


class HelpButton(tk.Label):
    """누르면 설명 창을 띄우는 작은 (?) 버튼."""

    def __init__(self, parent: tk.Widget, title: str, text, background: str = BACKGROUND) -> None:
        super().__init__(parent, text=" ? ", font=FONT_SMALL, background=GRID_LINE, foreground=TEXT,
                         cursor="hand2", padx=2)
        self._title = title
        self._text = text
        self.bind("<Button-1>", lambda _event: self.show())

    def show(self) -> None:
        text = self._text() if callable(self._text) else self._text
        window = tk.Toplevel(self)
        window.title(self._title)
        window.configure(background=BACKGROUND)
        window.transient(self.winfo_toplevel())
        body = tk.Text(window, wrap=tk.WORD, font=FONT_BODY, background=BACKGROUND, foreground=TEXT,
                       relief=tk.FLAT, padx=16, pady=12, width=60, height=min(30, text.count("\n") + 4))
        body.insert(tk.END, text)
        body.configure(state=tk.DISABLED)
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Button(window, text="닫기", command=window.destroy).pack(pady=(0, 10))


class Tooltip:
    """마우스를 올리면 잠시 뒤 위젯 아래에 작은 설명 창을 띄운다. text는 글자나, 띄울 때마다 부르는 함수.

    빈 글자를 돌려주면 띄우지 않는다. 안쪽 위젯 위로 옮겨 가도 <Leave>가 오지 않도록 묶음 프레임에 붙인다.
    """

    DELAY_MS = 400

    def __init__(self, widget: tk.Widget, text) -> None:
        self._widget = widget
        self._text = text
        self._window: Optional[tk.Toplevel] = None
        self._job: Optional[str] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self.hide, add="+")
        widget.bind("<ButtonPress>", self.hide, add="+")

    def _schedule(self, _event: object = None) -> None:
        self._cancel()
        self._job = self._widget.after(self.DELAY_MS, self.show)

    def _cancel(self) -> None:
        if self._job is not None:
            self._widget.after_cancel(self._job)
            self._job = None

    def show(self) -> None:
        self._cancel()
        self._destroy_window()
        text = self._text() if callable(self._text) else self._text
        if not text or not self._widget.winfo_ismapped():
            return
        window = tk.Toplevel(self._widget)
        window.wm_overrideredirect(True)
        label = tk.Label(window, text=text, justify=tk.LEFT, font=FONT_BODY, background=TOOLTIP_BACKGROUND,
                         foreground=TEXT, relief=tk.SOLID, borderwidth=1, padx=8, pady=4)
        label.pack()
        window.update_idletasks()
        # 화면 오른쪽 끝을 넘으면 왼쪽으로 당긴다(상단 바 오른쪽 위젯의 툴팁).
        x = min(self._widget.winfo_rootx(), self._widget.winfo_screenwidth() - window.winfo_reqwidth() - 4)
        y = self._widget.winfo_rooty() + self._widget.winfo_height() + 4
        window.geometry(f"+{max(x, 0)}+{y}")
        self._window = window

    def hide(self, _event: object = None) -> None:
        self._cancel()
        self._destroy_window()

    def _destroy_window(self) -> None:
        if self._window is not None:
            self._window.destroy()
            self._window = None


def section_title(parent: tk.Widget, text: str, help_text: str = "",
                  background: str = BACKGROUND) -> tk.Label:
    row = tk.Frame(parent, background=background)
    row.pack(fill=tk.X, pady=(6, 2))
    label = tk.Label(row, text=text, font=FONT_TITLE, background=background, foreground=TEXT, anchor="w")
    label.pack(side=tk.LEFT)
    if help_text:
        HelpButton(row, text, help_text, background=background).pack(side=tk.LEFT, padx=6)
    return label


class Table:
    """ttk.Treeview에 세로 스크롤과 행 비교 갱신을 붙인 표."""

    def __init__(self, parent: tk.Widget, columns: Sequence[Tuple[str, str, int, str]], height: int = 6,
                 on_select: Optional[Callable[[str], None]] = None,
                 on_double: Optional[Callable[[str], None]] = None) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND)
        keys = [column[0] for column in columns]
        self.tree = ttk.Treeview(self.frame, columns=keys, show="headings", height=height, selectmode="browse")
        for key, heading, width, anchor in columns:
            self.tree.heading(key, text=heading)
            self.tree.column(key, width=width, minwidth=40, anchor=anchor, stretch=True)
        scroll = ttk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        for level, color in LEVEL_COLORS.items():
            self.tree.tag_configure(level, foreground=color)
        self.tree.tag_configure("muted", foreground=MUTED)
        self.tree.tag_configure("text", foreground=TEXT)
        self._signature: Tuple = ()
        if on_select is not None:
            self.tree.bind("<<TreeviewSelect>>", lambda _event: self._selected(on_select))
        if on_double is not None:
            self.tree.bind("<Double-1>", lambda _event: self._selected(on_double))

    def _selected(self, callback: Callable[[str], None]) -> None:
        selection = self.tree.selection()
        if selection:
            callback(selection[0])

    def set_rows(self, rows: Iterable[Tuple[str, Sequence, str]]) -> bool:
        """rows는 (iid, 값들, 태그). 내용이 같으면 다시 그리지 않는다. 다시 그렸으면 True."""
        rows = list(rows)
        signature = tuple((iid, tuple(values), tag) for iid, values, tag in rows)
        if signature == self._signature:
            return False
        self._signature = signature
        selection = self.tree.selection()
        view = self.tree.yview()
        self.tree.delete(*self.tree.get_children())
        for iid, values, tag in rows:
            self.tree.insert("", tk.END, iid=iid, values=list(values), tags=(tag,) if tag else ())
        for iid in selection:
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
        self.tree.yview_moveto(view[0])
        return True

    def select(self, iid: str) -> None:
        if self.tree.exists(iid) and self.tree.selection() != (iid,):
            self.tree.selection_set(iid)
            self.tree.see(iid)


class LabelPlacer:
    """캔버스 글자가 서로 겹치지 않게 놓는다. 자리가 없으면 그리지 않는다."""

    OFFSETS = ((1, 0, "w"), (-1, 0, "e"), (0, -1, "s"), (0, 1, "n"), (1, -1, "sw"), (1, 1, "nw"),
               (-1, -1, "se"), (-1, 1, "ne"))

    def __init__(self, canvas: tk.Canvas, bounds: Optional[Box] = None) -> None:
        self.canvas = canvas
        self.bounds = bounds
        self.boxes: List[Box] = []

    def reserve(self, box: Box) -> None:
        self.boxes.append(box)

    def _free(self, box: Box) -> bool:
        if self.bounds is not None:
            left, top, right, bottom = self.bounds
            if box[0] < left or box[1] < top or box[2] > right or box[3] > bottom:
                return False
        return all(box[2] <= other[0] or box[0] >= other[2] or box[3] <= other[1] or box[1] >= other[3]
                   for other in self.boxes)

    def place(self, x: float, y: float, text: str, gap: float = 6.0, font: str = FONT_SMALL,
              color: str = TEXT, **options) -> Optional[int]:
        for dx, dy, anchor in self.OFFSETS:
            item = self.canvas.create_text(x + dx * gap, y + dy * gap, text=text, anchor=anchor, font=font,
                                           fill=color, **options)
            box = self.canvas.bbox(item)
            if box is not None and self._free(box):
                self.boxes.append(box)
                return item
            self.canvas.delete(item)
        return None


def text_height(font: str = FONT_SMALL) -> int:
    """글꼴 한 줄 높이(px). 캔버스 여백을 글자 크기에 맞출 때 쓴다(고정 px 여백은 글자 16에서 잘림)."""
    return tkfont.nametofont(font).metrics("linespace")


def text_width(text: str, font: str = FONT_SMALL) -> int:
    return tkfont.nametofont(font).measure(text)


def canvas_size(canvas: tk.Canvas, minimum_width: int = 100, minimum_height: int = 60) -> Tuple[int, int]:
    return max(canvas.winfo_width(), minimum_width), max(canvas.winfo_height(), minimum_height)


def empty_message(canvas: tk.Canvas, text: str) -> None:
    width, height = canvas_size(canvas)
    canvas.create_text(width / 2, height / 2, text=text, fill=MUTED, font=FONT_BODY, width=width - 40,
                       justify=tk.CENTER)


def nice_ticks(low: float, high: float, count: int = 5) -> List[float]:
    """low~high 범위를 1, 2, 5 단위의 보기 좋은 눈금으로 나눈다."""
    span = high - low
    if span <= 0 or not math.isfinite(span):
        return [low]
    raw = span / max(count, 1)
    magnitude = 10 ** math.floor(math.log10(raw))
    step = next(factor * magnitude for factor in (1, 2, 2.5, 5, 10) if factor * magnitude >= raw)
    first = math.ceil(low / step) * step
    ticks = []
    value = first
    while value <= high + step * 1e-9:
        ticks.append(round(value, 10))
        value += step
    return ticks


def padded_range(values: Iterable[float], include_zero: bool = False,
                 minimum_span: float = 1.0) -> Tuple[float, float]:
    finite = [value for value in values if math.isfinite(value)]
    if include_zero:
        finite.append(0.0)
    if not finite:
        return -minimum_span / 2, minimum_span / 2
    low, high = min(finite), max(finite)
    if high - low < minimum_span:
        middle = (high + low) / 2
        low, high = middle - minimum_span / 2, middle + minimum_span / 2
    margin = (high - low) * 0.08
    return low - margin, high + margin


class ChartArea:
    """데이터 좌표를 캔버스 좌표로 바꾸고 축과 계열을 그린다."""

    def __init__(self, canvas: tk.Canvas, box: Box, x_range: Tuple[float, float],
                 y_range: Tuple[float, float]) -> None:
        self.canvas = canvas
        self.left, self.top, self.right, self.bottom = box
        self.x_range = x_range
        self.y_range = y_range

    def x(self, value: float) -> float:
        low, high = self.x_range
        return self.left + (value - low) / (high - low) * (self.right - self.left)

    def y(self, value: float) -> float:
        low, high = self.y_range
        return self.bottom - (value - low) / (high - low) * (self.bottom - self.top)

    def x_value(self, pixel: float) -> float:
        low, high = self.x_range
        return low + (pixel - self.left) / (self.right - self.left) * (high - low)

    def draw_axes(self, x_format: Callable[[float], str], y_format: Callable[[float], str],
                  y_title: str = "", x_title: str = "", x_count: int = 5, y_count: int = 4) -> None:
        canvas = self.canvas
        for tick in nice_ticks(*self.y_range, count=y_count):
            y = self.y(tick)
            canvas.create_line(self.left, y, self.right, y, fill=GRID_LINE, width=2 if tick == 0 else 1)
            canvas.create_text(self.left - 6, y, text=y_format(tick), fill=MUTED, anchor="e", font=FONT_SMALL)
        for tick in nice_ticks(*self.x_range, count=x_count):
            x = self.x(tick)
            canvas.create_line(x, self.bottom, x, self.bottom + 4, fill=GRID_LINE)
            canvas.create_text(x, self.bottom + 6, text=x_format(tick), fill=MUTED, anchor="n", font=FONT_SMALL)
        canvas.create_line(self.left, self.bottom, self.right, self.bottom, fill=GRID_LINE)
        if y_title:
            canvas.create_text(self.left, self.top - 4, text=y_title, fill=MUTED, anchor="sw", font=FONT_SMALL)
        if x_title:
            canvas.create_text(self.right, self.top - 4, text=x_title, fill=MUTED, anchor="se", font=FONT_SMALL)

    def line(self, points: Sequence[Tuple[float, float]], color: str, width: int = 2,
             dash: Optional[tuple] = None) -> None:
        coordinates: List[float] = []
        for x, y in points:
            if not (math.isfinite(x) and math.isfinite(y)):
                self._flush(coordinates, color, width, dash)
                coordinates = []
                continue
            coordinates.extend((self.x(x), self.y(y)))
        self._flush(coordinates, color, width, dash)

    def _flush(self, coordinates: List[float], color: str, width: int, dash: Optional[tuple]) -> None:
        if len(coordinates) >= 4:
            self.canvas.create_line(*coordinates, fill=color, width=width, dash=dash)

    def hline(self, value: float, color: str, text: str = "", dash: tuple = (4, 3)) -> None:
        if not self.y_range[0] <= value <= self.y_range[1]:
            return
        y = self.y(value)
        self.canvas.create_line(self.left, y, self.right, y, fill=color, dash=dash, width=1)
        if text:
            self.canvas.create_text(self.right - 4, y - 2, text=text, fill=color, anchor="se", font=FONT_SMALL)

    def markers(self, points: Sequence[Tuple[float, float]], color: str, radius: float = 3.5) -> None:
        for x, y in points:
            if not (math.isfinite(x) and math.isfinite(y)):
                continue
            cx, cy = self.x(x), self.y(y)
            self.canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius, fill=color, outline="")

    def crosses(self, points: Sequence[Tuple[float, float]], color: str, size: float = 5.0) -> None:
        for x, y in points:
            cx, cy = self.x(x), self.y(y)
            self.canvas.create_line(cx - size, cy - size, cx + size, cy + size, fill=color, width=2)
            self.canvas.create_line(cx - size, cy + size, cx + size, cy - size, fill=color, width=2)


def histogram(canvas: tk.Canvas, values: Sequence[float], unit: str, reference: Optional[float] = None,
              color: str = CHART_COLOR, title: str = "") -> None:
    """값 분포 막대그래프. reference가 있으면 기준선을 그린다."""
    canvas.delete("all")
    width, height = canvas_size(canvas, 200, 120)
    if not values:
        empty_message(canvas, "값이 없습니다")
        return
    low, high = min(values), max(values)
    if reference is not None:
        low, high = min(low, reference), max(high, reference)
    if high - low < 1e-9:
        low, high = low - 1, high + 1
    bins = min(30, max(5, int(math.sqrt(len(values)))))
    step = (high - low) / bins
    counts = [0] * bins
    for value in values:
        index = min(int((value - low) / step), bins - 1)
        counts[index] += 1
    area = ChartArea(canvas, (56, 26, width - 16, height - 24), (low, high), (0, max(counts) * 1.15))
    area.draw_axes(lambda value: f"{value:g}", lambda value: f"{value:.0f}", "", unit, y_count=3)
    for index, count in enumerate(counts):
        if count == 0:
            continue
        x0 = area.x(low + index * step)
        x1 = area.x(low + (index + 1) * step)
        canvas.create_rectangle(x0 + 1, area.y(count), x1 - 1, area.y(0), fill=color, outline="")
    if reference is not None:
        x = area.x(reference)
        canvas.create_line(x, area.top, x, area.bottom, fill=TEXT, dash=(4, 3), width=2)
        canvas.create_text(x + 4, area.top + 2, text=f"기준 {reference:g}", anchor="nw", fill=TEXT, font=FONT_SMALL)
    if title:
        canvas.create_text(56, 4, text=title + " · 세로축은 개수", anchor="nw", fill=TEXT, font=FONT_SMALL)


CURSOR_SPACE = 7  # 진행 중 프레임 표시(▼) 자리


def frame_chart_heights() -> Tuple[int, int]:
    """빔 수 그래프가 다 보이는 높이와, 막대를 그릴 수 있는 최소 높이(px). 지금 글자 크기 기준."""
    line = text_height()
    core = CURSOR_SPACE + (line + 4 + 6) + 2 * line + (line + 10)  # 시간 띠(글자 포함), 막대 최소 2줄, 가로 눈금
    return core + (line + 4) + (line + 6) + line, core


def frame_request_chart(canvas: tk.Canvas, frames: Sequence[FrameShare], grid_cells: int = 0,
                        expected: Optional[float] = None, expected_text: str = "", max_frames: Optional[int] = None,
                        title: str = "", current: Optional[FrameShare] = None) -> None:
    """위: 마지막 탐색 프레임 시간 띠(탐색·확인·추적에 쓴 시간), 아래: 프레임별 끼어든 확인·추적 빔 수 막대.

    처리부는 10 ms마다 빔을 하나 보내고 탐색 빔은 프레임마다 격자 칸 수(189)로 고정이라, 점유율은 '한 프레임 시간을
    무엇에 썼나'와 같다. 시간 띠는 프레임이 닫힐 때마다 바뀌고, current(진행 중 프레임)를 주면 지금 위치를 ▼로 보인다.
    막대 오른쪽 축은 같은 높이의 확인·추적 비율(%) = n ÷ (탐색 + n)이다(탐색이 고정이라 개수와 1:1).
    expected(기대 추적 빔 수)를 주면 점선과 그래프 오른쪽 바깥 이름표, 계산식(expected_text)은 축 제목 줄에 자리가 있을 때만.
    높이가 모자라면 축 제목 줄 → 시간 띠 글자 → 요약 줄 순서로 빼서 막대를 남긴다. 여백은 글자 높이로 잡는다.
    """
    canvas.delete("all")
    width, height = canvas_size(canvas, 200, 40)
    if not frames:
        empty_message(canvas, "탐색 프레임이 한 번 다 돌면 프레임별 빔 수를 그립니다")
        return
    frames = list(frames)
    if max_frames is not None:
        frames = frames[-max_frames:]
    first, last = frames[0].index, frames[-1].index
    latest = frames[-1]
    searches = [frame.count(BeamType.SEARCH) for frame in frames]
    reference = grid_cells or Counter(searches).most_common(1)[0][0]
    observed = max(frame.request_beams for frame in frames)
    high = max(observed * 1.15, (expected or 0.0) * 2.0, 4.0)
    line = text_height()
    _full, core = frame_chart_heights()

    left = text_width(f"{high:.0f}") + 14
    expected_label = f"기대 {expected:.1f}" if expected is not None else ""
    # 넓으면 기대값 이름표에 비율도 붙인다(`기대 5.8 (3.0%)`). 좁으면 이름만.
    if expected_label and width >= 600:
        expected_label = f"{expected_label} ({expected / (reference + expected) * 100:.1f}%)"
    right = width - 12 - max(text_width(expected_label), text_width("99.9%")) - 8
    optional = {"summary": line + 4, "titles": line + 6}
    shown = set()
    used = core
    for name in ("summary", "titles"):
        if used + optional[name] + line <= height:
            shown.add(name)
            used += optional[name]

    # 요약 줄: 프레임 #n 1.95 s = 탐색 1.89 s(97%) + 확인 0.00 s + 추적 0.06 s. 넓이가 모자라면 줄인다.
    slot_ms = BEAM_SLOT_MS
    parts_ms = [(beam_type, latest.count(beam_type) * slot_ms) for beam_type in
                (BeamType.SEARCH, BeamType.CONFIRMATION, BeamType.TRACKING)]
    gap_ms = max(latest.duration_ms - sum(ms for _, ms in parts_ms), 0)
    y = 2.0
    if "summary" in shown:
        def pieces(level: int):
            head = [(f"{title} — " if title else "", MUTED), (f"프레임 #{latest.index}  " if level < 2 else
                                                              f"#{latest.index} ", TEXT),
                    (f"{latest.duration_ms / 1000:.2f} s = " if level < 2 else f"{latest.duration_ms / 1000:.2f} s: ",
                     TEXT)]
            body = []
            for number, (beam_type, ms) in enumerate(parts_ms):
                share = f"({latest.share(beam_type) * 100:.0f}%)" if level == 0 else ""
                join = "" if number == 0 else (" + " if level < 2 else " · ")
                unit = " s" if level < 2 else ""
                body.append((f"{join}{BEAM_NAMES[beam_type]} {ms / 1000:.2f}{unit}{share}", BEAM_COLORS[beam_type]))
            if gap_ms:
                body.append((f" + 빈 칸 {gap_ms / 1000:.2f} s", MUTED))
            return [piece for piece in head + body if piece[0]]

        for level in (0, 1, 2):
            chosen = pieces(level)
            if sum(text_width(text) for text, _ in chosen) <= width - 12 - left:
                break
        x = left
        for text, color in chosen:
            canvas.create_text(x, y, text=text, anchor="nw", fill=color, font=FONT_SMALL)
            x += text_width(text)
        y += line + 4

    # 시간 띠: 마지막 프레임 시간을 탐색·확인·추적·빈 칸으로 나눠 칠한다. 축 길이는 진행 중 프레임이 넘어가도 보이게 여유.
    elapsed_ms = current.duration_ms if current is not None else None
    scale_ms = max(latest.duration_ms, elapsed_ms or 0)
    end_text = f"{latest.duration_ms / 1000:.2f} s"
    bar_left, bar_right = left, width - 12 - text_width("99.99 s") - 6  # 띠 끝 프레임 시간 자리
    to_px = (bar_right - bar_left) / max(scale_ms, 1)
    cursor_top = y
    bar_top = y + CURSOR_SPACE
    bar_bottom = bar_top + line + 4
    x = bar_left
    segments = [(ms, blend(BEAM_COLORS[beam_type], 0.55, BACKGROUND)) for beam_type, ms in parts_ms]
    segments.append((gap_ms, GRID_LINE))
    for ms, color in segments:
        if ms <= 0:
            continue
        canvas.create_rectangle(x, bar_top, x + ms * to_px, bar_bottom, fill=color, outline="")
        x += ms * to_px
    search_ms = parts_ms[0][1]
    request_share = latest.share(BeamType.CONFIRMATION) + latest.share(BeamType.TRACKING)
    for text in (f"탐색 {latest.share(BeamType.SEARCH) * 100:.0f}% · 확인·추적 {request_share * 100:.0f}%",
                 f"탐색 {latest.share(BeamType.SEARCH) * 100:.0f}%"):
        if text_width(text) + 12 <= search_ms * to_px:
            canvas.create_text(bar_left + 6, (bar_top + bar_bottom) / 2, text=text, anchor="w", fill=TEXT,
                               font=FONT_SMALL)
            break
    canvas.create_text(x + 6, (bar_top + bar_bottom) / 2, text=end_text, anchor="w", fill=TEXT, font=FONT_SMALL)
    if elapsed_ms is not None:
        cx = bar_left + elapsed_ms * to_px
        canvas.create_line(cx, bar_top - 1, cx, bar_bottom + 1, fill=TEXT, width=2)
        canvas.create_polygon(cx - 5, cursor_top, cx + 5, cursor_top, cx, bar_top - 1, fill=TEXT, outline="")
    y = bar_bottom + 6

    if height < core:
        return

    top = y + (line + 6 if "titles" in shown else 4)
    area = ChartArea(canvas, (left, top, right, height - line - 10), (first - 0.5, last + 0.5), (0.0, high))
    titles = ("막대: 확인·추적 빔 수(개)", "확인·추적 비율") if "titles" in shown else ("", "")
    span = area.bottom - area.top
    y_count = 4 if span >= 6 * line else (3 if span >= 3 * line else 2)
    area.draw_axes(lambda value: f"{value:.0f}" if abs(value - round(value)) < 1e-6 else "",
                   lambda value: f"{value:.0f}", titles[0], "", y_count=y_count)
    if titles[1]:
        canvas.create_text(width - 12, top - 4, text=titles[1], anchor="se", fill=MUTED, font=FONT_SMALL)
    if "titles" in shown and expected_text:
        # 점선 계산식은 축 제목 줄의 빈자리에 쓴다(오른쪽 설명과 겹치면 생략. 도움말에도 있음).
        formula = f"┅ {expected_text}"
        start = left + text_width(titles[0]) + 16
        end = width - 12 - text_width(titles[1]) - 16
        if start + text_width(formula) <= end:
            canvas.create_text(start, top - 4, text=formula, anchor="sw", fill=TEXT, font=FONT_SMALL)
    # 오른쪽 % 축: 같은 높이의 개수 n을 비율 n ÷ (탐색 + n)으로. 기대값 이름표와 겹치는 눈금 글자는 뺀다.
    expected_y = area.y(expected) if expected is not None else None
    for tick in nice_ticks(*area.y_range, count=y_count):
        if tick <= 0:
            continue
        y_tick = area.y(tick)
        if expected_y is not None and abs(y_tick - expected_y) < line:
            continue
        canvas.create_text(area.right + 6, y_tick, text=f"{tick / (reference + tick) * 100:.1f}%", anchor="w",
                           fill=MUTED, font=FONT_SMALL)
    bar = max(min((area.right - area.left) / max(last - first + 1, 1) * 0.7, 18.0), 1.0)
    for frame in frames:
        center = area.x(frame.index)
        bottom = 0
        for beam_type in (BeamType.TRACKING, BeamType.CONFIRMATION):
            count = frame.count(beam_type)
            if count:
                canvas.create_rectangle(center - bar / 2, area.y(bottom + count), center + bar / 2, area.y(bottom),
                                        fill=BEAM_COLORS[beam_type], outline="")
                bottom += count
    if expected is not None:
        canvas.create_line(area.left, expected_y, area.right, expected_y, fill=TEXT, dash=(5, 3), width=2)
        canvas.create_text(area.right + 6, expected_y, text=expected_label, anchor="w", fill=TEXT, font=FONT_SMALL)


def bind_tree_colors(tree: ttk.Treeview, colors: Dict[str, str]) -> None:
    for tag, color in colors.items():
        tree.tag_configure(tag, foreground=color)
