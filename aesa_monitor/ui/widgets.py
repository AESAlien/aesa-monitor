"""여러 탭이 함께 쓰는 화면 부품: 판정 줄, 지표 카드, 표, 도움말, 캔버스 라벨 배치."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from aesa_monitor.core.verdict import Verdict
from aesa_monitor.ui.style import (
    BACKGROUND, FONT_BODY, FONT_SMALL, FONT_TITLE, FONT_VALUE, FONT_VERDICT, GRID_LINE, LEVEL_BACKGROUNDS,
    LEVEL_COLORS, LEVEL_NAMES, MUTED, PANEL, TEXT,
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


class KpiCard:
    """지표 하나: 제목, 큰 값, 기준이나 설명. 왼쪽 띠 색이 판정이다."""

    def __init__(self, parent: tk.Widget, title: str, help_text: str = "") -> None:
        self.frame = tk.Frame(parent, background=PANEL)
        self._stripe = tk.Frame(self.frame, background=LEVEL_COLORS["idle"], width=6)
        self._stripe.pack(side=tk.LEFT, fill=tk.Y)
        body = tk.Frame(self.frame, background=PANEL, padx=10, pady=6)
        body.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        header = tk.Frame(body, background=PANEL)
        header.pack(fill=tk.X)
        tk.Label(header, text=title, font=FONT_BODY, background=PANEL, foreground=MUTED,
                 anchor="w").pack(side=tk.LEFT)
        if help_text:
            HelpButton(header, title, help_text, background=PANEL).pack(side=tk.RIGHT)
        self._value = tk.Label(body, text="-", font=FONT_VALUE, background=PANEL, foreground=TEXT, anchor="w")
        self._value.pack(fill=tk.X)
        self._detail = tk.Label(body, text="", font=FONT_SMALL, background=PANEL, foreground=MUTED,
                                anchor="w", justify=tk.LEFT)
        self._detail.pack(fill=tk.X)

    def set(self, value: str, detail: str = "", level: str = "idle") -> None:
        self._value.configure(text=value)
        self._detail.configure(text=detail)
        self._stripe.configure(background=LEVEL_COLORS[level])


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
              color: str = "#378add", title: str = "") -> None:
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


def bind_tree_colors(tree: ttk.Treeview, colors: Dict[str, str]) -> None:
    for tag, color in colors.items():
        tree.tag_configure(tag, foreground=color)
