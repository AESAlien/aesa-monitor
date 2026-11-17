"""분석 탭 — 녹화와 처리부 로그로 "무슨 일이 있었나?"를 본다.

- 통계 요약: 빔 스케줄, 추적, 통신, 처리부 로그 항목별 값·기준·판정. 항목을 누르면 분포를 그린다.
  빔 점유율·탐색 프레임별 점유율 항목은 분포 대신 탐색 프레임별 빔 수(프레임 시간 띠 + 확인·추적 막대)를 그린다.
- 트랙별 요약: 시작, 수명, 미탐, 빔-탐지 차이 평균.
- 처리부 이벤트 타임라인: 메시지 로그의 운용·자세·요청 거부·경고를 처리부 시각에 놓는다.
  이벤트를 누르면 녹화 재생 위치를 그 시각 직전으로 옮긴다.
- 문제: 녹화를 열 때 처음부터 다시 계산해 자동으로 기록한 문제 사건(core/problems.py)과 처리부 로그의
  요청 거부·경고·오류. 누르면 재생 위치를 그 1초 전으로 옮긴다. 세션이 없으면 실시간으로 감지한 문제를 보여준다.
- 북마크(보조): 자동 기록이 못 잡은 것이나 원인 메모를 사람이 남긴다. 세션 옆 bookmarks.json에 저장한다.
  타임라인 맨 위 줄과 오른쪽 [북마크] 목록에 보이고, 누르면 재생 위치를 그 시각으로 옮긴다.
- 위(통계·트랙)와 아래(타임라인·목록) 사이 경계를 끌어 높이를 나눌 수 있다.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable, Dict, List, Optional

from aesa_monitor.core.problems import BAD as LEVEL_BAD, Problem, level_counts
from aesa_monitor.core.state import MonitorState
from aesa_monitor.io.processor_log import (
    CATEGORY_ATTITUDE, CATEGORY_LIFECYCLE, CATEGORY_OPERATION, CATEGORY_OTHER, CATEGORY_PROBLEM,
    CATEGORY_REJECTED, LogEvent,
)
from aesa_monitor.io.bookmarks import Bookmark, sort_bookmarks
from aesa_monitor.io.replay import ReplayPosition
from aesa_monitor.io.session import Session
from aesa_monitor.ui.bookmark_dialog import BookmarkDialog
from aesa_monitor.ui.style import (
    blend, BACKGROUND, BAD, CHART_COLOR, FONT_BODY, FONT_SMALL, GRID_LINE, LEVEL_COLORS, LEVEL_NAMES, MUTED, OK, PANEL, TEXT,
    WARN, format_time,
)
from aesa_monitor.ui.widgets import (
    HelpButton, LabelPlacer, Table, canvas_size, empty_message, frame_request_chart, histogram, nice_ticks,
    section_title,
)

TIMELINE_LANES = (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM, CATEGORY_LIFECYCLE)
EVENT_FILTERS = {
    "중요": (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM),
    "전체": (CATEGORY_OPERATION, CATEGORY_ATTITUDE, CATEGORY_REJECTED, CATEGORY_PROBLEM, CATEGORY_LIFECYCLE,
           CATEGORY_OTHER),
    "거부만": (CATEGORY_REJECTED,),
    "경고·오류만": (CATEGORY_PROBLEM,),
}
HISTOGRAM_REFERENCES = {"연속 빔 간격": 10.0, "연속 탐지 간격": 10.0}
BOOKMARK_COLORS = {"이슈": BAD, "확인 필요": WARN, "참고": MUTED}
BOOKMARK_TAGS = {"이슈": "bad", "확인 필요": "warn", "참고": "text"}
PROBLEM_FILTERS = ("전체", "이상만", "빔", "추적", "통신", "처리부 로그")
PROBLEM_LEAD_S = 1.0  # 문제를 누르면 이만큼 앞에서 재생을 시작해 직전 상황을 본다
HELP_PROBLEMS = (
    "모니터가 문제 상황을 자동으로 기록한 목록입니다. 사람이 누르지 않아도 남습니다.\n\n"
    "• 기준은 실시간 판정과 같습니다: 빠진 빔, 빔 없음, 탐색 프레임 시간·추적 주기·탐색→확인 지연 이탈, 트랙 미탐·종료, "
    "확인 실패, 처리부 연결 끊김·상대 연결 안 됨, 처리부 재시작, 처리부 로그의 요청 거부·경고·오류.\n"
    "• 모니터 추정이 섞인 값(예측-측정 잔차, 연관 탐지 선택과 그에 기댄 빔 안 위치)은 기록하지 않습니다.\n"
    "• 같은 종류(트랙 문제는 같은 트랙)가 2초 안에 다시 생기면 한 건으로 합치고 '횟수'에 셉니다.\n"
    "• 녹화를 열 때 처음부터 다시 계산합니다. 기준을 고치면 예전 녹화에도 그대로 적용됩니다.\n"
    "• 행이나 타임라인 '문제' 줄을 누르면 재생 위치를 그 1초 전으로 옮기고, 트랙 문제면 추적 탭에서 그 트랙을 고릅니다.\n"
    "• 오른쪽 클릭 → '북마크로 남기기'로 원인 메모를 달아 둘 수 있습니다.\n"
    "• 세션을 열지 않았으면 모니터를 켠 뒤 실시간으로 감지한 문제를 보여줍니다(녹화해 두어야 나중에 그 위치로 갈 수 있음)."
)
HELP_BOOKMARKS = (
    "자동 기록(문제 목록)을 돕는 기능입니다. 자동 기록이 못 잡은 시점(또는 구간)이나 원인 메모를 직접 남깁니다.\n\n"
    "• 추가: [+ 북마크] 버튼이나 B 키(글자 입력 칸 밖에서, 한글 입력 상태가 아닐 때). 시작 시각은 지금 재생 위치로 채워집니다.\n"
    "• 구간: 끝 시각도 넣으면 타임라인에 띠로 그립니다.\n"
    "• 이동: 목록 행이나 타임라인 ▼·띠를 누르면 재생 위치를 그 시각으로 옮기고, 트랙을 넣었으면 추적 탭에서 그 트랙을 고릅니다.\n"
    "• 편집: 목록 행을 두 번 누르거나 오른쪽 클릭 → 편집. 삭제: 오른쪽 클릭 → 삭제, 또는 Delete 키.\n"
    "• 저장: 세션 폴더의 bookmarks.json(파일 하나를 열었으면 '<파일 이름>.bookmarks.json')에 바로 씁니다. "
    "녹화 폴더와 함께 넘기면 다른 사람도 같은 북마크를 봅니다.\n\n"
    "시각은 처리부 시각입니다(이벤트 타임라인과 같은 기준)."
)

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
                 select_run: Callable[[int], None], focus_time: Callable[..., None],
                 current_position: Callable[[], Optional[ReplayPosition]],
                 live_problems: Callable[[], List[Problem]]) -> None:
        self.frame = tk.Frame(parent, background=BACKGROUND, padx=12, pady=8)
        self._focus_event = focus_event
        self._focus_time = focus_time
        self._current_position = current_position
        self._live_problems = live_problems
        self._problems: List[Problem] = []
        self._problem_signature: tuple = ()
        self._problem_filter = tk.StringVar(value=PROBLEM_FILTERS[0])
        self._problem_points: List[tuple] = []
        self._bookmark_points: List[tuple] = []
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

        # 위(통계·트랙)와 아래(타임라인·목록)를 경계로 나눈다. 창이 낮으면 한쪽이 눌리므로 사람이 경계를 끌어 조절한다.
        # 처음 경계는 위 50%로 둔다(_place_sash).
        self._panes = ttk.PanedWindow(self.frame, orient=tk.VERTICAL)
        self._panes.pack(fill=tk.BOTH, expand=True, pady=(6, 0))
        body = tk.Frame(self._panes, background=BACKGROUND)
        bottom = tk.Frame(self._panes, background=BACKGROUND)
        self._panes.add(body, weight=3)
        self._panes.add(bottom, weight=2)
        self._sash_placed = False
        self._panes.bind("<Configure>", self._place_sash)
        body.columnconfigure(0, weight=3, uniform="analysis")
        body.columnconfigure(1, weight=2, uniform="analysis")
        body.rowconfigure(0, weight=1)

        left = tk.Frame(body, background=BACKGROUND)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        section_title(left, "통계 요약", "녹화(또는 탐지 CSV) 전체를 훑어 계산합니다. 판정 기준은 실시간 탭과 같습니다.\n"
                      "항목을 누르면 아래에 값의 분포와 설명을 보여줍니다.")
        # 통계 표와 분포 그래프 사이도 경계를 끌어 높이를 나눈다. 처음에는 표에 60%를 준다(_place_sash).
        self._left_panes = ttk.PanedWindow(left, orient=tk.VERTICAL)
        self._left_panes.pack(fill=tk.BOTH, expand=True)
        stats_pane = tk.Frame(self._left_panes, background=BACKGROUND)
        chart_pane = tk.Frame(self._left_panes, background=BACKGROUND)
        self._left_panes.add(stats_pane, weight=3)
        self._left_panes.add(chart_pane, weight=2)
        self._left_sash_placed = False
        self._left_panes.bind("<Configure>", self._place_left_sash)
        self._stats = Table(stats_pane, (
            ("name", "항목", 170, tk.W), ("value", "값", 330, tk.W), ("reference", "기준", 150, tk.W),
            ("level", "판정", 60, tk.CENTER),
        ), height=3, on_select=self._on_stat_selected)
        self._stats.tree.tag_configure("group", font="AesaBold", background=PANEL)
        self._stats.frame.pack(fill=tk.BOTH, expand=True)
        self._note = tk.Label(chart_pane, text="", font=FONT_SMALL, background=BACKGROUND, foreground=MUTED,
                              anchor="w", justify=tk.LEFT)
        self._note.pack(fill=tk.X)
        self._histogram = tk.Canvas(chart_pane, background=BACKGROUND, highlightthickness=0, height=80)
        self._histogram.pack(fill=tk.BOTH, expand=True)
        self._histogram.bind("<Configure>", lambda _event: self._draw_histogram())
        self._selected_stat: Optional[int] = None

        right = tk.Frame(body, background=BACKGROUND)
        right.grid(row=0, column=1, sticky="nsew")
        section_title(right, "트랙별 요약", "확인·추적 빔을 받은 요청 ID별 요약입니다. 시작은 첫 확인(또는 추적) 빔의 처리부 시각입니다.\n"
                      "상태: 진행(녹화 끝까지 빔이 나감), 종료(중간에 빔이 끊김), 확인 실패(추적 빔이 나간 적 없음).\n"
                      "행을 두 번 누르면 재생 위치를 그 트랙의 마지막 빔 직전으로 옮깁니다.")
        self._tracks = Table(right, (
            ("id", "ID", 50, tk.CENTER), ("start", "시작", 80, tk.E), ("life", "수명", 70, tk.E),
            ("beams", "추적 빔", 70, tk.E), ("misses", "미탐/최대연속", 110, tk.E), ("difference", "차이 평균", 80, tk.E),
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
        HelpButton(timeline_header, "북마크", HELP_BOOKMARKS).pack(side=tk.RIGHT, padx=(0, 16))
        ttk.Button(timeline_header, text="+ 북마크 (B)", command=self.add_bookmark).pack(side=tk.RIGHT, padx=(0, 4))
        lower = tk.Frame(bottom, background=BACKGROUND)
        lower.pack(fill=tk.BOTH, expand=True)
        lower.columnconfigure(0, weight=3)
        lower.columnconfigure(1, weight=2)
        lower.rowconfigure(0, weight=1)
        self._timeline = tk.Canvas(lower, background=BACKGROUND, highlightthickness=0, height=150)
        self._timeline.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        self._timeline.bind("<Button-1>", self._on_timeline_click)
        self._timeline.bind("<Configure>", lambda _event: self._draw_timeline())

        # 오른쪽 목록: [이벤트 | 북마크]
        self._lists = ttk.Notebook(lower)
        self._lists.grid(row=0, column=1, sticky="nsew")
        # [문제] 자동 기록
        self._problem_page = tk.Frame(self._lists, background=BACKGROUND)
        self._lists.add(self._problem_page, text="문제")
        problem_bar = tk.Frame(self._problem_page, background=BACKGROUND)
        problem_bar.pack(fill=tk.X)
        ttk.Combobox(problem_bar, textvariable=self._problem_filter, values=PROBLEM_FILTERS, state="readonly",
                     width=10).pack(side=tk.LEFT)
        self._problem_filter.trace_add("write", lambda *_args: self._fill_problems(force=True))
        self._problem_summary = tk.Label(problem_bar, text="", font=FONT_SMALL, background=BACKGROUND,
                                         foreground=MUTED, anchor="w")
        self._problem_summary.pack(side=tk.LEFT, padx=8)
        HelpButton(problem_bar, "문제 자동 기록", HELP_PROBLEMS).pack(side=tk.RIGHT)
        self._problem_note = tk.Label(self._problem_page, text="", font=FONT_SMALL, background=BACKGROUND,
                                      foreground=MUTED, anchor="w", justify=tk.LEFT)
        self._problem_note.pack(side=tk.BOTTOM, fill=tk.X)
        self._problem_note.bind("<Configure>", lambda event: self._problem_note.configure(
            wraplength=max(event.width - 8, 100)))
        self._problem_table = Table(self._problem_page, (
            ("time", "처리부 시각", 110, tk.E), ("level", "수준", 60, tk.CENTER), ("category", "분류", 60, tk.CENTER),
            ("title", "내용", 260, tk.W), ("count", "횟수", 50, tk.E),
        ), height=3, on_select=self._on_problem_selected)
        self._problem_table.frame.pack(fill=tk.BOTH, expand=True)
        self._problem_menu = tk.Menu(self.frame, tearoff=False)
        self._problem_menu.add_command(label="북마크로 남기기 (원인 메모)", command=self._bookmark_selected_problem)
        self._problem_table.tree.bind("<Button-3>", self._show_problem_menu)

        self._event_table = Table(self._lists, (
            ("time", "처리부 시각", 100, tk.E), ("category", "분류", 80, tk.CENTER), ("text", "내용", 320, tk.W),
        ), height=4, on_select=self._on_event_selected)
        self._lists.add(self._event_table.frame, text="이벤트")
        bookmark_page = tk.Frame(self._lists, background=BACKGROUND)
        self._bookmark_page = bookmark_page
        self._lists.add(bookmark_page, text="북마크")
        # 버튼 줄은 두지 않는다(낮은 창에서 목록이 가려짐). 추가는 위 [+ 북마크]·B 키, 편집은 두 번 누르기,
        # 편집·삭제는 오른쪽 클릭 메뉴와 Delete 키로 한다.
        self._bookmark_note = tk.Label(bookmark_page, text="", font=FONT_SMALL, background=BACKGROUND,
                                       foreground=MUTED, anchor="w", justify=tk.LEFT)
        self._bookmark_note.pack(side=tk.BOTTOM, fill=tk.X)
        self._bookmark_note.bind("<Configure>", lambda event: self._bookmark_note.configure(
            wraplength=max(event.width - 8, 100)))
        self._bookmark_table = Table(bookmark_page, (
            ("time", "처리부 시각", 150, tk.E), ("tag", "분류", 70, tk.CENTER), ("title", "제목", 220, tk.W),
            ("track", "트랙", 50, tk.CENTER),
        ), height=3, on_select=self._on_bookmark_selected, on_double=lambda _iid: self._edit_selected_bookmark())
        self._bookmark_table.frame.pack(fill=tk.BOTH, expand=True)
        self._bookmark_menu = tk.Menu(self.frame, tearoff=False)
        self._bookmark_menu.add_command(label="편집", command=self._edit_selected_bookmark)
        self._bookmark_menu.add_command(label="삭제", command=self._delete_selected_bookmark)
        self._bookmark_menu.add_separator()
        self._bookmark_menu.add_command(label="새 북마크 추가", command=self.add_bookmark)
        tree = self._bookmark_table.tree
        tree.bind("<Button-3>", self._show_bookmark_menu)
        tree.bind("<Delete>", lambda _event: self._delete_selected_bookmark())

    # 세션

    def set_session(self, session: Optional[Session]) -> None:
        self._session = session
        if session is None:
            self._info.configure(text=GUIDE)
            self._run_box.configure(values=())
            self._run_box.set("")
            self._stats.set_rows([])
            self._tracks.set_rows([])
            self._fill_bookmarks()
            self._fill_problems(force=True)
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
        self._fill_bookmarks()
        self._fill_problems(force=True)
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
                    "-" if track.mean_angle_error_m is None else f"{track.mean_angle_error_m:.1f} m", state,
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
        if item.chart == "frames":
            frame_request_chart(canvas, report.frames, title=f"탐색 프레임 {len(report.frames)}개")
            return
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
                  LEVEL_COLORS.get(item.level, CHART_COLOR) if item.level != "idle" else CHART_COLOR,
                  f"{item.name} 분포 ({len(item.samples)}개)")

    def _on_track_double(self, iid: str) -> None:
        report = self._session.report if self._session else None
        if report is None:
            return
        track = report.tracks[int(iid)]
        self._focus_event(LogEvent(None, track.last_ms, "monitor", "info", "", CATEGORY_REJECTED,
                                   {"requestId": str(track.track_id)}))

    def _place_sash(self, event: tk.Event) -> None:
        """처음 한 번만 경계를 둔다: 위(통계·트랙) 50%, 아래(타임라인·문제 목록) 50%. 그 뒤는 사람이 끈 대로 둔다."""
        if self._sash_placed or event.height < 200:
            return
        self._sash_placed = True
        self._panes.sashpos(0, int(event.height * 0.50))

    def _place_left_sash(self, event: tk.Event) -> None:
        # 바깥 경계를 둔 뒤 안쪽 칸 크기가 정해졌을 때 한 번만 둔다(그 전에는 높이가 0~1이라 표가 사라진다).
        if self._left_sash_placed or not self._sash_placed or event.height < 120:
            return
        self._left_sash_placed = True
        self._left_panes.sashpos(0, int(event.height * 0.55))

    # 북마크

    @property
    def bookmarks(self) -> List[Bookmark]:
        return self._session.bookmarks if self._session is not None else []

    def _fill_bookmarks(self) -> None:
        rows = []
        for index, bookmark in enumerate(self.bookmarks):
            time_text = format_time(bookmark.start_ms) if not bookmark.is_range else (
                f"{format_time(bookmark.start_ms)}~{format_time(bookmark.end_ms)}")
            rows.append((str(index), (time_text, bookmark.tag, bookmark.title,
                                      "-" if bookmark.track_id is None else f"#{bookmark.track_id}"),
                         BOOKMARK_TAGS.get(bookmark.tag, "text")))
        if not rows:
            text = ("세션을 열면 북마크를 달 수 있습니다" if self._session is None else
                    "북마크 없음 — [+ 북마크]나 B 키로 추가")
            rows.append(("none", ("", "", text, ""), "muted"))
        self._bookmark_table.set_rows(rows)
        self._lists.tab(self._bookmark_page, text=f"북마크 ({len(self.bookmarks)})" if self.bookmarks else "북마크")
        self._bookmark_note.configure(text="")

    def _show_bookmark_menu(self, event: tk.Event) -> None:
        tree = self._bookmark_table.tree
        row = tree.identify_row(event.y)
        if row and row.isdigit():
            tree.selection_set(row)
        state = tk.NORMAL if self._selected_bookmark() is not None else tk.DISABLED
        self._bookmark_menu.entryconfigure("편집", state=state)
        self._bookmark_menu.entryconfigure("삭제", state=state)
        self._bookmark_menu.tk_popup(event.x_root, event.y_root)

    def _selected_bookmark(self) -> Optional[int]:
        selection = self._bookmark_table.tree.selection()
        if selection and selection[0].isdigit():
            return int(selection[0])
        return None

    def add_bookmark(self) -> None:
        """지금 재생 위치(없으면 빈 칸)로 북마크 추가 창을 연다."""
        if self._session is None:
            messagebox.showinfo("북마크", "녹화나 처리부 로그를 연 뒤에 북마크를 달 수 있습니다.",
                                parent=self.frame)
            return
        # 어느 탭에서 불렀든(B 키) 지금 재생 위치를 그때 묻는다. 분석 탭이 마지막으로 그려진 시각을 쓰면 안 된다.
        position = self._current_position()
        start_ms = position.processor_ms if position is not None else None

        def saved(bookmark: Bookmark) -> None:
            self._place(bookmark, position)
            self._add_saved(bookmark)

        BookmarkDialog(self.frame, saved, start_ms=start_ms, track_ids=self._track_ids(),
                       save_path=str(self._session.bookmark_file))

    def _place(self, bookmark: Bookmark, position: Optional[ReplayPosition] = None,
               recording_s: Optional[float] = None, segment: Optional[int] = None) -> None:
        """북마크에 녹화 위치와 처리부 실행 구간을 채운다(처리부 재시작이 있어도 위치가 하나로 정해지게).

        창에서 시각을 바꾸지 않았으면 열 때의 녹화 위치를 그대로 쓰고, 바꿨으면 같은 구간 안에서 처리부 시각으로 다시 찾는다.
        """
        player = self._session.player if self._session else None
        if position is not None:
            recording_s, segment = position.recording_s, position.segment
            if bookmark.start_ms != position.processor_ms:
                recording_s = None
        if player is None:
            return
        if segment is None and recording_s is not None:
            segment = player.segment_at(recording_s)
        bookmark.segment = segment
        bookmark.recording_s = (recording_s if recording_s is not None else
                                player.time_for_processor_ms(bookmark.start_ms, segment))
        bookmark.end_recording_s = (None if bookmark.end_ms is None else
                                    player.time_for_processor_ms(bookmark.end_ms, segment))

    def _track_ids(self) -> List[int]:
        report = self._session.report if self._session else None
        return sorted({track.track_id for track in report.tracks}) if report is not None else []

    def _add_saved(self, bookmark: Bookmark) -> None:
        self._store(self.bookmarks + [bookmark], bookmark)

    def _edit_selected_bookmark(self) -> None:
        index = self._selected_bookmark()
        if index is None or self._session is None:
            return
        original = self.bookmarks[index]

        def saved(bookmark: Bookmark) -> None:
            # 시각을 그대로 두었으면 원래 녹화 위치를, 바꿨으면 같은 처리부 실행 구간 안에서 다시 찾는다.
            same = bookmark.start_ms == original.start_ms
            self._place(bookmark, recording_s=original.recording_s if same else None, segment=original.segment)
            if same and bookmark.end_ms == original.end_ms:
                bookmark.end_recording_s = original.end_recording_s
            bookmarks = list(self.bookmarks)
            bookmarks[index] = bookmark
            self._store(bookmarks, bookmark)

        BookmarkDialog(self.frame, saved, track_ids=self._track_ids(), bookmark=original,
                       save_path=str(self._session.bookmark_file))

    def _delete_selected_bookmark(self) -> None:
        index = self._selected_bookmark()
        if index is None or self._session is None:
            return
        bookmark = self.bookmarks[index]
        if not messagebox.askyesno("북마크 삭제", f"'{bookmark.title}' 북마크를 지울까요?", parent=self.frame):
            return
        self._store([item for position, item in enumerate(self.bookmarks) if position != index], None)

    def _store(self, bookmarks: List[Bookmark], select: Optional[Bookmark]) -> None:
        """목록을 바꾸고 바로 파일에 쓴다. 쓰지 못하면 화면 목록은 바꾼 채로 두고 알린다."""
        session = self._session
        session.bookmarks = sort_bookmarks(bookmarks)
        try:
            session.save_bookmarks()
        except OSError as error:
            messagebox.showerror("북마크 저장 실패", f"{session.bookmark_file}\n{error}\n\n"
                                 "화면의 북마크는 남아 있지만 프로그램을 끄면 사라집니다.", parent=self.frame)
        self._fill_bookmarks()
        self._lists.select(self._bookmark_page)
        if select is not None and select in session.bookmarks:
            self._bookmark_table.select(str(session.bookmarks.index(select)))
        self._draw_timeline()

    def _on_bookmark_selected(self, iid: str) -> None:
        if not iid.isdigit():
            return
        bookmark = self.bookmarks[int(iid)]
        self._bookmark_note.configure(text=bookmark.note or "(메모 없음)")
        self._focus_time(bookmark.start_ms, bookmark.track_id, bookmark.recording_s, bookmark.segment)

    # 문제 (자동 기록)

    def _source_problems(self) -> List[Problem]:
        if self._session is not None:
            return self._session.problems()
        return list(self._live_problems())

    def _filtered(self, problems: List[Problem]) -> List[Problem]:
        choice = self._problem_filter.get()
        if choice == "이상만":
            return [problem for problem in problems if problem.level == LEVEL_BAD]
        if choice in ("빔", "추적", "통신", "처리부 로그"):
            return [problem for problem in problems if problem.category == choice]
        return problems

    def _fill_problems(self, force: bool = False) -> None:
        source = self._source_problems()
        signature = (id(self._session), len(source),
                     tuple((problem.count, problem.level, problem.end_ms) for problem in source[-5:]))
        if not force and signature == self._problem_signature:
            return
        self._problem_signature = signature
        self._problems = self._filtered(source)
        rows = []
        for index, problem in enumerate(self._problems):
            time_text = format_time(problem.start_ms) if problem.start_ms is not None else "-"
            if problem.is_range:
                time_text += f" +{problem.duration_ms / 1000:.1f}s"
            level = "■ 이상" if problem.level == LEVEL_BAD else "▲ 주의"
            segment = f"[실행 {problem.segment + 1}] " if problem.segment else ""
            rows.append((str(index), (time_text, level, problem.category, segment + problem.title,
                                      problem.count), "bad" if problem.level == LEVEL_BAD else "warn"))
        if not rows:
            if self._session is None:
                text = "실시간으로 감지한 문제 없음" if not source else "고른 분류의 문제 없음"
            else:
                text = "문제 없음" if not source else "고른 분류의 문제 없음"
            rows.append(("none", ("", "", "", text, ""), "muted"))
        self._problem_table.set_rows(rows)
        counts = level_counts(source)
        title = f"문제 ({counts['bad'] + counts['warn']})" if source else "문제"
        self._lists.tab(self._problem_page, text=title)
        origin = "녹화에서 다시 계산" if self._session is not None and self._session.player is not None else (
            "처리부 로그" if self._session is not None else "실시간")
        self._problem_summary.configure(text=f"이상 {counts['bad']} · 주의 {counts['warn']} · {origin}")
        self._draw_timeline()

    def _selected_problem(self) -> Optional[Problem]:
        selection = self._problem_table.tree.selection()
        if selection and selection[0].isdigit() and int(selection[0]) < len(self._problems):
            return self._problems[int(selection[0])]
        return None

    def _on_problem_selected(self, iid: str) -> None:
        if not iid.isdigit() or int(iid) >= len(self._problems):
            return
        problem = self._problems[int(iid)]
        lines = [problem.detail] if problem.detail else []
        if problem.count > 1:
            lines.append(f"{problem.count}회 합침")
        if self._session is None:
            lines.append("실시간 기록 — 녹화해 둔 세션을 열면 이 위치로 이동할 수 있습니다")
        elif problem.start_s is None:
            lines.append("녹화가 없어 재생 위치로 이동할 수 없습니다")
        self._problem_note.configure(text=" · ".join(lines))
        if self._session is not None:
            recording_s = None if problem.start_s is None else max(problem.start_s - PROBLEM_LEAD_S, 0.0)
            lead_ms = None if problem.start_ms is None else problem.start_ms - int(PROBLEM_LEAD_S * 1000)
            self._focus_time(lead_ms, problem.track_id, recording_s, problem.segment)

    def _show_problem_menu(self, event: tk.Event) -> None:
        tree = self._problem_table.tree
        row = tree.identify_row(event.y)
        if row and row.isdigit():
            tree.selection_set(row)
        state = tk.NORMAL if self._selected_problem() is not None and self._session is not None else tk.DISABLED
        self._problem_menu.entryconfigure(0, state=state)
        self._problem_menu.tk_popup(event.x_root, event.y_root)

    def _bookmark_selected_problem(self) -> None:
        problem = self._selected_problem()
        if problem is None or self._session is None or problem.start_ms is None:
            return
        template = Bookmark(problem.start_ms, problem.title, end_ms=problem.end_ms if problem.is_range else None,
                            tag="이슈" if problem.level == LEVEL_BAD else "확인 필요", track_id=problem.track_id,
                            note=problem.detail)

        def saved(bookmark: Bookmark) -> None:
            same = bookmark.start_ms == problem.start_ms
            self._place(bookmark, recording_s=problem.start_s if same else None, segment=problem.segment)
            self._add_saved(bookmark)

        dialog = BookmarkDialog(self.frame, saved, track_ids=self._track_ids(), bookmark=template,
                                save_path=str(self._session.bookmark_file))
        dialog.window.title("문제를 북마크로 남기기")

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
        hits = [item for item in self._problem_points if item[0] <= event.x <= item[2] and item[1] <= event.y <= item[3]]
        if hits:
            index = min(hits, key=lambda item: abs((item[0] + item[2]) / 2 - event.x))[4]
            self._lists.select(self._problem_page)
            self._problem_table.select(str(index))
            self._on_problem_selected(str(index))
            return
        for left, top, right, bottom, index in self._bookmark_points:
            if left - 4 <= event.x <= right + 4 and top <= event.y <= bottom:
                self._lists.select(self._bookmark_page)
                self._bookmark_table.select(str(index))
                self._on_bookmark_selected(str(index))
                return
        if not self._timeline_points:
            return
        x, y, index = min(self._timeline_points, key=lambda item: abs(item[0] - event.x) + abs(item[1] - event.y))
        if abs(x - event.x) <= 12 and abs(y - event.y) <= 16:
            self._event_table.select(str(index))
            self._focus_event(self._events[index])

    def refresh(self, state: Optional[MonitorState]) -> None:
        """재생 위치 선을 갱신한다. 세션이 없으면 실시간 문제 목록을 갱신한다."""
        if self._session is None:
            self._fill_problems()
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
        self._bookmark_points = []
        self._problem_points = []
        problems = self._problems if session is not None else []
        for problem in problems:
            if problem.start_ms is not None:
                times.append(problem.start_ms)
        for bookmark in self.bookmarks:
            times.append(bookmark.start_ms)
            if bookmark.end_ms is not None:
                times.append(bookmark.end_ms)
        if not times:
            empty_message(canvas, "처리부 메시지 로그를 열면 이벤트를 시간축에 보여줍니다")
            return
        low, high = min(times), max(times)
        if high - low < 1000:
            high = low + 1000
        label_width = 80
        left, right = label_width, width - 12
        band = 14
        mark_lane = 16
        problem_lane = 16
        lanes_top = band + mark_lane + problem_lane
        lane_height = (height - 24 - lanes_top) / len(TIMELINE_LANES)

        def to_x(value: float) -> float:
            return left + (value - low) / (high - low) * (right - left)

        if recording_range:
            canvas.create_rectangle(to_x(recording_range[0]), 2, to_x(recording_range[1]), band - 2,
                                    fill=GRID_LINE, outline="")
            canvas.create_text(left - 8, band / 2, text="녹화", anchor="e", fill=MUTED, font=FONT_SMALL)
        for index, lane in enumerate(TIMELINE_LANES):
            top = lanes_top + index * lane_height
            canvas.create_rectangle(left, top + 1, right, top + lane_height - 1, fill=PANEL, outline="")
            canvas.create_text(left - 8, top + lane_height / 2, text=lane, anchor="e", fill=MUTED, font=FONT_SMALL)
        for tick in nice_ticks(low / 1000, high / 1000, max(3, int((right - left) / 100))):
            x = to_x(tick * 1000)
            canvas.create_line(x, height - 22, x, height - 18, fill=GRID_LINE)
            canvas.create_text(x, height - 17, text=format_time(tick * 1000)[:-4], anchor="n", fill=MUTED,
                               font=FONT_SMALL)
        visible = {id(event): index for index, event in enumerate(self._events)}
        colors = {"bad": BAD, "warn": WARN, "ok": OK, "info": TEXT}
        self._draw_bookmarks(canvas, to_x, left, band, mark_lane, height - 22)
        self._draw_problem_lane(canvas, problems, to_x, left, right, band + mark_lane, problem_lane)
        placer = LabelPlacer(canvas, (left, lanes_top, right, height - 22))
        for event in all_events:
            if event.processor_ms is None or event.category not in TIMELINE_LANES:
                continue
            lane = TIMELINE_LANES.index(event.category)
            x = to_x(event.processor_ms)
            y = lanes_top + lane * lane_height + lane_height / 2
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

    def _draw_problem_lane(self, canvas: tk.Canvas, problems: List[Problem], to_x: Callable[[float], float],
                           left: float, right: float, top: float, lane: float) -> None:
        """타임라인 '문제' 줄: 자동 기록한 문제를 수준 색으로. 구간은 막대, 한 시점은 짧은 세로 막대."""
        canvas.create_text(left - 8, top + lane / 2, text="문제", anchor="e", fill=MUTED, font=FONT_SMALL)
        canvas.create_rectangle(left, top + 1, right, top + lane - 1, fill=PANEL, outline="")
        selected = self._selected_problem()
        for index, problem in enumerate(problems):
            if problem.start_ms is None:
                continue
            color = BAD if problem.level == LEVEL_BAD else WARN
            x0 = to_x(problem.start_ms)
            x1 = max(to_x(problem.end_ms), x0 + 3) if problem.end_ms is not None else x0 + 3
            outline = TEXT if problem is selected else ""
            canvas.create_rectangle(x0, top + 3, x1, top + lane - 3, fill=color, outline=outline,
                                    width=2 if outline else 1)
            self._problem_points.append((x0 - 2, top, x1 + 2, top + lane, index))

    def _draw_bookmarks(self, canvas: tk.Canvas, to_x: Callable[[float], float], left: float, band: float,
                        lane: float, bottom: float) -> None:
        """타임라인 맨 위 '북마크' 줄: 한 시점은 ▼와 세로 점선, 구간은 막대와 옅은 띠. 번호는 목록 순서(B1, B2…)."""
        canvas.create_text(left - 8, band + lane / 2, text="북마크", anchor="e", fill=MUTED, font=FONT_SMALL)
        middle = band + lane / 2
        for number, bookmark in enumerate(self.bookmarks, 1):
            color = BOOKMARK_COLORS.get(bookmark.tag, MUTED)
            x0 = to_x(bookmark.start_ms)
            if bookmark.end_ms is not None:
                x1 = max(to_x(bookmark.end_ms), x0 + 3)
                # 줄 바탕(회색) 위, 이벤트 점 아래에 오도록 줄 바탕을 그린 뒤·이벤트를 그리기 전에 그린다.
                canvas.create_rectangle(x0, band + lane, x1, bottom, fill=blend(color, 0.12, BACKGROUND), outline="")
                canvas.create_rectangle(x0, band + 4, x1, band + lane - 4, fill=color, outline="")
                label_x = x1 + 3
                box = (x0, band, x1, bottom)
            else:
                canvas.create_line(x0, band + lane, x0, bottom, fill=color, dash=(3, 3))
                canvas.create_polygon(x0, band + lane - 2, x0 - 6, band + 3, x0 + 6, band + 3, fill=color, outline="")
                label_x = x0 + 8
                box = (x0 - 6, band, x0 + 6, band + lane)
            canvas.create_text(label_x, middle, text=f"B{number}", anchor="w", fill=color, font=FONT_SMALL)
            self._bookmark_points.append((*box, number - 1))


def stat_colors() -> Dict[str, str]:
    return dict(LEVEL_COLORS)


