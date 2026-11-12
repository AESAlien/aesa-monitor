"""분석 탭 — 녹화와 처리부 로그로 "무슨 일이 있었나?"를 본다.

- 통계 요약: 빔 스케줄, 추적, 통신, 처리부 로그 항목별 값·기준·판정. 항목을 누르면 분포를 그린다.
- 트랙별 요약: 시작, 수명, 미탐, 빔-탐지 차이.
- 처리부 이벤트 타임라인: 메시지 로그의 운용·자세·요청 거부·경고를 처리부 시각에 놓는다.
  이벤트를 누르면 녹화 재생 위치를 그 시각 직전으로 옮긴다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Dict, List, Optional

from aesa_monitor.core.state import MonitorState
from aesa_monitor.io.processor_log import (
    CATEGORY_ATTITUDE, CATEGORY_LIFECYCLE, CATEGORY_OPERATION, CATEGORY_OTHER, CATEGORY_PROBLEM,
    CATEGORY_REJECTED, LogEvent,
)
from aesa_monitor.io.session import Session
from aesa_monitor.ui.style import (
    BACKGROUND, BAD, FONT_BODY, FONT_SMALL, GRID_LINE, LEVEL_COLORS, LEVEL_NAMES, MUTED, OK, PANEL, TEXT, WARN,
    format_time,
)
from aesa_monitor.ui.widgets import LabelPlacer, Table, canvas_size, empty_message, histogram, nice_ticks, section_title

TIMELINE_LANES = (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM, CATEGORY_LIFECYCLE)
EVENT_FILTERS = {
    "중요": (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM),
    "전체": (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM, CATEGORY_LIFECYCLE,
           CATEGORY_OTHER),
    "거부만": (CATEGORY_REJECTED,),
    "경고·오류만": (CATEGORY_PROBLEM,),
}
HISTOGRAM_REFERENCES = {"연속 빔 간격": 10.0, "연속 탐지 간격": 10.0}

GUIDE = (
    "분석할 데이터를 여세요.\n\n"
    "• 모니터 녹화: 위쪽 [● 녹화]로 녹화하면 ~/aesa-records/<시각>/ 에 저장됩니다. 재생과 모든 통계가 됩니다.\n"
    "• 처리부 로그: [로그 가져오기]로 처리부의 adp_*.log, adp_detection_*.csv를 가져옵니다.\n"
    "  로그만 있으면 빔 방향과 트랙 목록이 없어서 일부 통계만 나옵니다.\n"
    "• [폴더 열기]로 세션 폴더(녹화+로그)를 한 번에 엽니다."
)


def event_level(event: LogEvent) -> str:
    if event.level in ("error", "critical"):
        return "bad"
    if event.level in ("warning", "warn"):
        return "warn"
    return "ok" if event.category == CATEGORY_OPERATION else "info"


class AnalysisTab:
    def __init__(self, parent: tk.Widget, open_folder: Callable[[], None], open_file: Callable[[], None],
                 fetch_logs: Callable[[], None], focus_event: Callable[[LogEvent], None],
                 select_run: Callable[[int], None]) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND, padx=12, pady=8)
        self._focus_event = focus_event
        self._select_run = select_run
        self._session: Optional[Session] = None
        self._events: List[LogEvent] = []
        self._timeline_points: List[tuple] = []
        self._filter = tk.StringVar(value="중요")
        self._current_ms: Optional[int] = None

        header = tk.Frame(self.frame, background=PANEL, padx=12, pady=8)
        header.pack(fill=tk.X)
        buttons = tk.Frame(header, background=PANEL)
        buttons.pack(side=tk.RIGHT, anchor="n")
        ttk.Button(buttons, text="폴더 열기", command=open_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="파일 열기", command=open_file).pack(side=tk.LEFT, padx=2)
        ttk.Button(buttons, text="로그 가져오기", command=fetch_logs).pack(side=tk.LEFT, padx=2)
        self._info = tk.Label(header, text=GUIDE, font=FONT_BODY, background=PANEL, foreground=TEXT, anchor="nw",
                              justify=tk.LEFT)
        self._info.pack(side=tk.LEFT, fill=tk.X, expand=True)
        run_row = tk.Frame(self.frame, background=BACKGROUND)
        run_row.pack(fill=tk.X, pady=(6, 0))
        tk.Label(run_row, text="처리부 실행", background=BACKGROUND, foreground=MUTED).pack(side=tk.LEFT)
        self._run_box = ttk.Combobox(run_row, state="readonly", width=48)
        self._run_box.pack(side=tk.LEFT, padx=6)
        self._run_box.bind("<<ComboboxSelected>>", lambda _event: self._select_run(self._run_box.current()))
        tk.Label(run_row, text="한 폴더에 처리부를 여러 번 실행한 로그가 있으면 여기서 고릅니다", background=BACKGROUND,
                 foreground=MUTED, font=FONT_SMALL).pack(side=tk.LEFT, padx=6)

        # 아래쪽 이벤트 타임라인이 화면 밖으로 밀리지 않게 먼저 아래에 붙인다.
        bottom = tk.Frame(self.frame, background=BACKGROUND)
        bottom.pack(side=tk.BOTTOM, fill=tk.X)
        body = tk.Frame(self.frame, background=BACKGROUND)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=3, uniform="analysis")
        body.columnconfigure(1, weight=2, uniform="analysis")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, background=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        section_title(left, "통계 요약", "녹화(또는 탐지 CSV) 전체를 훑어 계산합니다. 판정 기준은 실시간 탭과 같습니다.\n"
                      "항목을 누르면 아래에 값의 분포와 설명을 보여줍니다.")
        self._stats = Table(left, (
            ("name", "항목", 170, tk.W), ("value", "값", 330, tk.W), ("reference", "기준", 150, tk.W),
            ("level", "판정", 60, tk.CENTER),
        ), height=8, on_select=self._on_stat_selected)
        self._stats.tree.tag_configure("group", font="AesaBold", background=PANEL)
        self._stats.frame.pack(fill=tk.BOTH, expand=True)
        self._note = tk.Label(left, text="", font=FONT_SMALL, background=BACKGROUND, foreground=MUTED, anchor="w",
                              justify=tk.LEFT)
        self._note.pack(fill=tk.X)
        self._histogram = tk.Canvas(left, background=BACKGROUND, highlightthickness=0, height=140)
        self._histogram.pack(fill=tk.X)
        self._histogram.bind("<Configure>", lambda _event: self._draw_histogram())
        self._selected_stat: Optional[int] = None

        right = tk.Frame(body, background=BACKGROUND)
        right.grid(row=0, column=1, sticky="nsew")
        section_title(right, "트랙별 요약", "확인·추적 빔을 받은 요청 ID별 요약입니다. 시작은 첫 확인(또는 추적) 빔의 처리부 시각입니다.\n"
                      "상태: 진행(녹화 끝까지 빔이 나감), 종료(중간에 빔이 끊김), 확인 실패(추적 빔이 나간 적 없음).\n"
                      "행을 두 번 누르면 재생 위치를 그 트랙의 마지막 빔 직전으로 옮깁니다.")
        self._tracks = Table(right, (
            ("id", "ID", 50, tk.CENTER), ("start", "시작", 80, tk.E), ("life", "수명", 70, tk.E),
            ("beams", "추적 빔", 70, tk.E), ("misses", "미탐/최대연속", 110, tk.E), ("rms", "RMS", 70, tk.E),
            ("state", "상태", 80, tk.CENTER),
        ), height=12, on_double=self._on_track_double)
        self._tracks.frame.pack(fill=tk.BOTH, expand=True)

        timeline_header = tk.Frame(bottom, background=BACKGROUND)
        timeline_header.pack(fill=tk.X)
        section_title(timeline_header, "처리부 이벤트 타임라인",
                      "처리부 메시지 로그(adp_*.log)의 이벤트를 처리부 시각에 놓았습니다.\n"
                      "처리부 시각 0은 로그의 'Six worker threads started' 줄입니다(수 ms 오차, 추정).\n"
                      "위쪽 회색 띠는 녹화가 있는 구간, 세로 선은 현재 재생 위치입니다. "
                      "점이나 목록 행을 누르면 재생 위치를 그 이벤트 1초 전으로 옮깁니다.").master.pack(side=tk.LEFT)
        for name in reversed(list(EVENT_FILTERS)):
            ttk.Radiobutton(timeline_header, text=name, value=name, variable=self._filter,
                            command=self._refresh_events).pack(side=tk.RIGHT, padx=2)
        lower = tk.Frame(bottom, background=BACKGROUND)
        lower.pack(fill=tk.BOTH, expand=False)
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        self._timeline = tk.Canvas(lower, background=BACKGROUND, highlightthickness=0, height=150)
        self._timeline.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._timeline.bind("<Button-1>", self._on_timeline_click)
        self._timeline.bind("<Configure>", lambda _event: self._draw_timeline())
        self._event_table = Table(lower, (
            ("time", "처리부 시각", 100, tk.E), ("category", "분류", 80, tk.CENTER), ("text", "내용", 320, tk.W),
        ), height=5, on_select=self._on_event_selected)
        self._event_table.frame.grid(row=0, column=1, sticky="nsew")

    # 세션

    def set_session(self, session: Optional[Session]) -> None:
        self._session = session
        if session is None:
            self._info.configure(text=GUIDE)
            self._run_box.configure(values=())
            self._run_box.set("")
            self._stats.set_rows([])
            self._tracks.set_rows([])
            self._refresh_events()
            return
        self._info.configure(text="\n".join(session.describe()))
        labels = [run.label for run in session.runs]
        self._run_box.configure(values=labels)
        if session.run is not None:
            self._run_box.current(session.runs.index(session.run))
        else:
            self._run_box.set("(처리부 로그 없음)")
        self._fill_stats()
        self._fill_tracks()
        self._refresh_events()

    def _fill_stats(self) -> None:
        report = self._session.report if self._session else None
        rows = []
        if report is None:
            rows.append(("none", ("통계 없음", "녹화나 탐지 CSV가 필요합니다", "", ""), "muted"))
        else:
            group = None
            for index, item in enumerate(report.items):
                if item.group != group:
                    group = item.group
                    rows.append((f"group-{index}", (group, "", "", ""), "group"))
                rows.append((str(index), (item.name, item.value, item.reference,
                                          LEVEL_NAMES.get(item.level, "") if item.level != "idle" else ""),
                             item.level if item.level != "idle" else "text"))
        self._stats.set_rows(rows)
        self._histogram.delete("all")
        self._selected_stat = None
        self._draw_histogram()
        self._note.configure(text="")

    def _fill_tracks(self) -> None:
        report = self._session.report if self._session else None
        rows = []
        if report is not None:
            for index, track in enumerate(report.tracks):
                if not track.confirmed:
                    state, tag = "확인 실패", "muted"
                elif track.ended:
                    state, tag = "종료", "warn" if track.max_consecutive_misses else "text"
                else:
                    state, tag = "진행", "ok"
                rows.append((str(index), (
                    f"#{track.track_id}", f"{track.first_ms / 1000:.1f} s", f"{track.lifetime_s:.0f} s",
                    track.tracking_beams, f"{track.misses} / {track.max_consecutive_misses}",
                    "-" if track.rms_angle_error_m is None else f"{track.rms_angle_error_m:.1f} m", state,
                ), tag))
        self._tracks.set_rows(rows)

    def _on_stat_selected(self, iid: str) -> None:
        report = self._session.report if self._session else None
        if report is None or not iid.isdigit():
            return
        self._selected_stat = int(iid)
        self._note.configure(text=report.items[self._selected_stat].note)
        self._draw_histogram()

    def _draw_histogram(self) -> None:
        canvas = self._histogram
        canvas.delete("all")
        report = self._session.report if self._session else None
        if report is None or self._selected_stat is None:
            empty_message(canvas, "통계 항목을 누르면 값의 분포를 여기에 그립니다")
            return
        item = report.items[self._selected_stat]
        if not item.samples:
            empty_message(canvas, "이 항목은 분포가 없습니다")
            return
        reference = HISTOGRAM_REFERENCES.get(item.name)
        if reference is None and item.reference:
            try:
                reference = float(item.reference.split()[0])
            except ValueError:
                reference = None
        histogram(canvas, item.samples, item.unit, reference,
                  LEVEL_COLORS.get(item.level, "#378add") if item.level != "idle" else "#378add",
                  f"{item.name} 분포 ({len(item.samples)}개)")

    def _on_track_double(self, iid: str) -> None:
        report = self._session.report if self._session else None
        if report is None:
            return
        track = report.tracks[int(iid)]
        self._focus_event(LogEvent(None, track.last_ms, "monitor", "info", "", CATEGORY_REJECTED,
                                   {"requestId": str(track.track_id)}))

    # 이벤트

    def _visible_events(self) -> List[LogEvent]:
        log = self._session.message_log if self._session else None
        if log is None:
            return []
        categories = EVENT_FILTERS[self._filter.get()]
        return [event for event in log.events if event.category in categories]

    def _refresh_events(self) -> None:
        self._events = self._visible_events()
        rows = []
        for index, event in enumerate(self._events):
            rows.append((str(index), (format_time(event.processor_ms), event.category, event.summary),
                         {"bad": "bad", "warn": "warn", "ok": "ok"}.get(event_level(event), "text")))
        if not rows:
            text = "메시지 로그 없음" if self._session is None or self._session.message_log is None else "해당 이벤트 없음"
            rows.append(("none", ("", "", text), "muted"))
        self._event_table.set_rows(rows)
        self._draw_timeline()

    def _on_event_selected(self, iid: str) -> None:
        if iid.isdigit():
            self._focus_event(self._events[int(iid)])

    def _on_timeline_click(self, event: tk.Event) -> None:
        if not self._timeline_points:
            return
        x, y, index = min(self._timeline_points, key=lambda item: abs(item[0] - event.x) + abs(item[1] - event.y))
        if abs(x - event.x) <= 12 and abs(y - event.y) <= 16:
            self._event_table.select(str(index))
            self._focus_event(self._events[index])

    def refresh(self, state: Optional[MonitorState]) -> None:
        """재생 위치 선을 갱신한다."""
        current = state.now_ms() if state is not None else None
        if current != self._current_ms:
            self._current_ms = current
            self._draw_timeline()

    def _draw_timeline(self) -> None:
        canvas = self._timeline
        canvas.delete("all")
        width, height = canvas_size(canvas, 200, 100)
        self._timeline_points = []
        session = self._session
        all_events = session.message_log.events if session and session.message_log else []
        times = [event.processor_ms for event in all_events if event.processor_ms is not None]
        recording_range = session.player.processor_range_ms() if session and session.player else None
        if recording_range:
            times.extend(recording_range)
        if not times:
            empty_message(canvas, "처리부 메시지 로그를 열면 이벤트를 시간축에 보여줍니다")
            return
        low, high = min(times), max(times)
        if high - low < 1000:
            high = low + 1000
        label_width = 80
        left, right = label_width, width - 12
        band = 14
        lane_height = (height - 24 - band) / len(TIMELINE_LANES)

        def to_x(value: float) -> float:
            return left + (value - low) / (high - low) * (right - left)

        if recording_range:
            canvas.create_rectangle(to_x(recording_range[0]), 2, to_x(recording_range[1]), band - 2,
                                    fill=GRID_LINE, outline="")
            canvas.create_text(left - 8, band / 2, text="녹화", anchor="e", fill=MUTED, font=FONT_SMALL)
        for index, lane in enumerate(TIMELINE_LANES):
            top = band + index * lane_height
            canvas.create_rectangle(left, top + 1, right, top + lane_height - 1, fill=PANEL, outline="")
            canvas.create_text(left - 8, top + lane_height / 2, text=lane, anchor="e", fill=MUTED, font=FONT_SMALL)
        for tick in nice_ticks(low / 1000, high / 1000, max(3, int((right - left) / 100))):
            x = to_x(tick * 1000)
            canvas.create_line(x, height - 22, x, height - 18, fill=GRID_LINE)
            canvas.create_text(x, height - 17, text=format_time(tick * 1000)[:-4], anchor="n", fill=MUTED,
                               font=FONT_SMALL)
        visible = {id(event): index for index, event in enumerate(self._events)}
        colors = {"bad": BAD, "warn": WARN, "ok": OK, "info": TEXT}
        placer = LabelPlacer(canvas, (left, band, right, height - 22))
        for event in all_events:
            if event.processor_ms is None or event.category not in TIMELINE_LANES:
                continue
            lane = TIMELINE_LANES.index(event.category)
            x = to_x(event.processor_ms)
            y = band + lane * lane_height + lane_height / 2
            color = colors[event_level(event)]
            selected = id(event) in visible
            radius = 5 if selected else 3
            canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color if selected else PANEL,
                               outline=color, width=2)
            if selected:
                self._timeline_points.append((x, y, visible[id(event)]))
                if event.category == CATEGORY_REJECTED:
                    placer.place(x, y, f"#{event.details.get('requestId', '?')}", gap=7, color=TEXT)
        if self._current_ms is not None and low <= self._current_ms <= high:
            x = to_x(self._current_ms)
            canvas.create_line(x, 0, x, height - 22, fill=TEXT, width=2)


def stat_colors() -> Dict[str, str]:
    return dict(LEVEL_COLORS)
